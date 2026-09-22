"""Pi runtime adapter (pi-coding-agent CLI), following the CodexRuntime pattern.

The adapter shells out to the ``pi`` CLI in non-interactive JSON mode
(``pi --print --mode json ...``). The Runtime protocol, receipt boundaries,
process-tree supervision, cancellation and timeout semantics are identical to
``CodexRuntime``; only the command line and the JSON event stream differ.

Offline discipline (incident 2026-09-17): development probes and regressions
for this adapter use mock executables only and assert no network activity;
real provider calls happen exclusively in the approved Track C canary. The
adapter is deterministic: it never inherits the ambient environment — HOME,
PI_* directories and any provider credentials come only from the caller-staged
``Invocation.environment`` — which also removes the stale-host
``PI_PACKAGE_DIR`` ENOENT failure observed on 2026-09-17.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .codex import ProcessTree
from .contracts import Capabilities, Invocation, RuntimeResult

MAX_TEXT = 1_048_576
MAX_LINE = 1_048_576
ENGINE = "pi-cli"
ENGINE_VERSION = "0.85.1"


class PiRuntime:
    def __init__(self, executable: Path):
        if not executable.is_absolute():
            raise ValueError("Pi executable must be an absolute local path")
        self.executable = executable

    def capabilities(self) -> Capabilities:
        return Capabilities(
            engine=ENGINE, engine_version=ENGINE_VERSION, cancel=os.name == "posix"
        )

    def command(
        self, invocation: Invocation, session: str | None, output: Path
    ) -> list[str]:
        """pi --print --mode json with the minimal ambient surface.

        The prompt is delivered on stdin (never argv) so option-looking prompt
        text cannot be parsed as flags. Resume uses ``--resume <handle>`` with
        the previous native session id; the session directory is pinned into
        the sandboxed home so a resumed handle resolves inside the isolated
        workspace. ``--api-key`` is deliberately never used: provider
        credentials travel only via the caller-staged environment.
        ``invocation.provider`` is required by the contract for pi-cli and is
        mapped to ``--provider``; validate() already rejects None.
        """
        if invocation.scope.engine != ENGINE or invocation.provider is None:
            raise ValueError("pi runtime requires an explicit provider")
        cmd = [
            str(self.executable),
            "--print",
            "--mode",
            "json",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-context-files",
            "--no-themes",
            "--provider",
            invocation.provider,
        ]
        if invocation.model:
            cmd += ["--model", invocation.model]
        if session:
            cmd += ["--resume", session]
        return cmd

    @staticmethod
    def environment(invocation: Invocation) -> dict[str, str]:
        # Never inherit the node's ambient environment or home/auth/config.
        env = dict(invocation.environment)
        for key in env:
            if key.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_")):
                raise ValueError(
                    "server credentials are not runtime environment inputs"
                )
        home = str(invocation.runtime_home)
        env["HOME"] = home
        # PI_PACKAGE_DIR/PI_CONFIG_DIR are *installed-asset* paths, not user
        # config. Overriding them (e.g. to runtime_home) makes --version read
        # an empty package.json and report 0.0.0, breaking the run. They are
        # deliberately REMOVED so the CLI resolves its own installation dir;
        # host leakage is prevented because the base is the caller-staged
        # environment, never the ambient one. Session location is controlled
        # by the --session-dir flag in command() instead of an env override.
        env.pop("PI_PACKAGE_DIR", None)
        env.pop("PI_CONFIG_DIR", None)
        env["PI_SESSION_DIR"] = str(invocation.runtime_home / "sessions")
        return env

    async def _version_matches(
        self, invocation: Invocation, env: dict[str, str]
    ) -> bool:
        proc = await asyncio.create_subprocess_exec(
            str(self.executable),
            "--version",
            cwd=invocation.workspace,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
            # The installed CLI prints just its version ("0.85.1"); match on
            # the pinned version token instead of an exact banner format —
            # format drift must not wedge every run into UNSUPPORTED_RUNTIME.
            return proc.returncode == 0 and ENGINE_VERSION.encode() in stdout
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def run(
        self,
        invocation: Invocation,
        session_handle: str | None,
        emit: Callable[[str, dict], None],
        launched: Callable[[int], None],
        authorized: Callable[[], bool],
    ) -> RuntimeResult:
        invocation.validate()
        if os.name != "posix":
            return RuntimeResult("failed", "not_started", "UNSUPPORTED_RUNTIME")
        env = self.environment(invocation)
        try:
            if not await self._version_matches(invocation, env):
                return RuntimeResult("failed", "not_started", "UNSUPPORTED_RUNTIME")
        except asyncio.CancelledError:
            return RuntimeResult("cancelled", "not_started", "cancelled")
        except (TimeoutError, OSError):
            return RuntimeResult("failed", "not_started", "UNSUPPORTED_RUNTIME")

        if not authorized():
            return RuntimeResult("failed", "not_started", "POLICY_DENIED")

        with tempfile.TemporaryDirectory(prefix="anygarden-pi-") as directory:
            output = Path(directory) / "last-message.txt"
            task = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    *self.command(invocation, session_handle, output),
                    cwd=invocation.workspace,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                    limit=MAX_LINE,
                )
            )
            cancelled_at_spawn = False
            try:
                proc = await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled_at_spawn = True
                proc = await task
            except OSError:
                return RuntimeResult("failed", "not_started", "ENGINE_ERROR")
            tree = ProcessTree(proc.pid)
            stream: asyncio.Task | None = None
            result = RuntimeResult("unknown", "unknown", "runtime_error")
            try:
                launched(proc.pid)
                if cancelled_at_spawn or not authorized():
                    raise asyncio.CancelledError
                stream = asyncio.create_task(
                    self._collect(
                        proc, invocation, session_handle, output, emit, authorized
                    )
                )
                deadline = (
                    asyncio.get_running_loop().time() + invocation.timeout_seconds
                )
                while not stream.done():
                    tree.observe()
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait({stream}, timeout=min(0.05, remaining))
                result = stream.result()
            except TimeoutError:
                result = RuntimeResult("failed", "stopped", "TIMEOUT_STOPPED")
            except asyncio.CancelledError:
                result = RuntimeResult("cancelled", "stopped", "cancelled")
            except Exception:  # noqa: BLE001 — reap child before reporting unknown
                result = RuntimeResult("unknown", "unknown", "invalid_runtime_output")
            finally:
                async def cleanup() -> bool:
                    confirmed = await tree.stop()
                    if stream is not None and not stream.done():
                        stream.cancel()
                    if stream is not None:
                        await asyncio.gather(stream, return_exceptions=True)
                    try:
                        await asyncio.wait_for(proc.wait(), 2)
                    except TimeoutError:
                        confirmed = False
                    return confirmed

                cleanup_task = asyncio.create_task(cleanup())
                try:
                    confirmed = await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    confirmed = await cleanup_task
                    result = RuntimeResult("cancelled", "stopped", "cancelled")
            if not confirmed:
                return RuntimeResult("unknown", "unknown", "termination_unconfirmed")
            return result

    async def _collect(
        self, proc, invocation, session, output, emit, authorized
    ) -> RuntimeResult:
        prompt = (
            f"{invocation.instructions}\n\n{invocation.prompt}"
            if invocation.instructions
            else invocation.prompt
        )
        if not authorized():
            return RuntimeResult("failed", "stopped", "POLICY_DENIED")
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        texts: list[str] = []
        text_size = 0
        usage = None
        session_handle = session
        failed = False
        settled = False
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "session":
                handle = event.get("id")
                if isinstance(handle, str) and 0 < len(handle) <= 256:
                    session_handle = handle
            elif kind == "message_end":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                raw_usage = message.get("usage")
                if isinstance(raw_usage, dict):
                    # Multi-turn runs emit usage per assistant message; the
                    # receipt must report the SUM across the run, not the
                    # last message's counters (P1, task #68 review follow-up).
                    # Non-numeric/negative values never corrupt the total.
                    if usage is None:
                        usage = {"input_tokens": 0, "output_tokens": 0}
                    for target, source_key in (
                        ("input_tokens", "input"),
                        ("output_tokens", "output"),
                    ):
                        value = raw_usage.get(source_key)
                        if isinstance(value, int) and value > 0:
                            usage[target] += value
                if message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get("type") == "text"
                            and isinstance(block.get("text"), str)
                        ):
                            text_size += len(block["text"].encode())
                            if text_size > MAX_TEXT:
                                raise ValueError("runtime output exceeds limit")
                            texts.append(block["text"])
                if message.get("stopReason") == "error":
                    failed = True
                    # errorMessage is deliberately not captured: provider error
                    # payloads never leave this boundary (audit gets the code,
                    # not the payload).
            elif kind == "agent_end":
                if event.get("willRetry") is True:
                    continue
            elif kind == "agent_settled":
                settled = True
            elif kind in {"agent_start", "turn_start", "message_start", "turn_end"}:
                emit("progress", {"event": kind})
        await proc.wait()
        if proc.returncode != 0:
            return RuntimeResult("failed", "stopped", "ENGINE_ERROR")
        if failed:
            return RuntimeResult("failed", "stopped", "ENGINE_ERROR")
        if not settled:
            return RuntimeResult("unknown", "stopped", "missing_terminal_event")
        text = "\n".join(texts)
        if output.exists():
            with output.open("rb") as file:
                raw = file.read(MAX_TEXT + 1)
            if len(raw) > MAX_TEXT:
                return RuntimeResult("unknown", "stopped", "output_limit")
            text = raw.decode(errors="replace").strip() or text
        if not text:
            return RuntimeResult("failed", "stopped", "ENGINE_ERROR")
        return RuntimeResult(
            "succeeded", "finished", "completed", text, session_handle, usage
        )
