"""Codex subprocess boundary, independent of ChatClient and room WS."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

import psutil

from .contracts import Capabilities, Invocation, RuntimeResult
from .endpoint import codex_endpoint_arguments, validate_endpoint_invocation
from .failure_feedback import classify_failure

_MEASURED_USAGE: ContextVar[dict | None] = ContextVar("measured_usage", default=None)

MAX_TEXT = 1_048_576
MAX_LINE = 1_048_576
ENGINE = "codex-cli"
# An empty catalog list means Codex versions are observed, not gated. CLI
# compatibility is determined by the actual exec/result protocol.
SUPPORTED_VERSIONS: tuple[str, ...] = ()


class ProcessTree:
    """Own one POSIX session and remember observed detached descendants.

    This is process supervision, not a sandbox against hostile daemonization.
    Unverifiable members yield unknown, never confirmed cancellation.
    """

    def __init__(self, pid: int):
        self.pid = pid
        self.seen: dict[int, psutil.Process] = {}
        self.uncertain = False

    def observe(self) -> None:
        try:
            root = psutil.Process(self.pid)
            for process in [root, *root.children(recursive=True)]:
                self.seen[process.pid] = process
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            self.uncertain = True
        # Members can survive their group leader and be reparented.
        for process in psutil.process_iter():
            try:
                if os.getpgid(process.pid) == self.pid:
                    self.seen[process.pid] = process
            except (ProcessLookupError, psutil.NoSuchProcess):
                pass
            except PermissionError:
                # An inaccessible, unrelated process is not evidence of ownership.
                continue

    def live(self) -> list[psutil.Process]:
        self.observe()
        alive = []
        for process in self.seen.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(process)
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                self.uncertain = True
        return alive

    async def stop(self) -> bool:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            alive = self.live()
            if not alive:
                return not self.uncertain
            try:
                os.killpg(self.pid, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                self.uncertain = True
            for process in alive:
                try:
                    process.send_signal(sig)
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error:
                    self.uncertain = True
            deadline = asyncio.get_running_loop().time() + (
                0.25 if sig == signal.SIGTERM else 1.0
            )
            while self.live() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
        return not self.live() and not self.uncertain


class CodexRuntime:
    def __init__(self, executable: Path):
        if not executable.is_absolute():
            raise ValueError("Codex executable must be an absolute local path")
        self.executable = executable
        # Observed once for session fencing and diagnostics; never a gate.
        self.observed_version: str | None = None
        self._version_checked = False

    def capabilities(self) -> Capabilities:
        return Capabilities(
            engine_version=self.observed_version or "unknown",
            cancel=os.name == "posix",
        )

    def command(
        self, invocation: Invocation, session: str | None, output: Path
    ) -> list[str]:
        endpoint = validate_endpoint_invocation(invocation)
        cmd = [str(self.executable), "exec"]
        if session:
            cmd += ["resume", session]
        sandbox = (
            "read-only"
            if invocation.permission_level == "restricted"
            else "workspace-write"
        )
        cmd += [
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-c",
            f"sandbox_mode={sandbox}",
            "-c",
            "approval_policy=never",
        ]
        if invocation.model:
            cmd += ["-m", invocation.model]
        if invocation.reasoning_effort:
            cmd += [
                "-c",
                f"model_reasoning_effort={json.dumps(invocation.reasoning_effort)}",
            ]
        if endpoint is None and invocation.provider is not None:
            cmd += ["-c", f"model_provider={json.dumps(invocation.provider)}"]
        cmd += codex_endpoint_arguments(endpoint)
        return cmd + ["-o", str(output), "-"]

    @staticmethod
    def environment(invocation: Invocation) -> dict[str, str]:
        # Never inherit the node's ambient environment or home/auth/config.
        validate_endpoint_invocation(invocation)
        env = dict(invocation.environment)
        for key in env:
            if key.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_")):
                raise ValueError(
                    "server credentials are not runtime environment inputs"
                )
        env["CODEX_HOME"] = str(invocation.runtime_home)
        env["HOME"] = str(invocation.runtime_home)
        return env

    async def observe_version(self, cwd: Path, env: dict[str, str]) -> None:
        """Read the CLI version once without preventing an execution attempt."""
        if self._version_checked:
            return
        self._version_checked = True
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.executable),
                "--version",
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
            if proc.returncode == 0:
                out = stdout.decode(errors="replace").strip()
                self.observed_version = (
                    out.removeprefix("codex-cli ").strip()[:64] or None
                )
        except TimeoutError:
            pass
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
            await self.observe_version(invocation.workspace, env)
        except asyncio.CancelledError:
            return RuntimeResult("cancelled", "not_started", "cancelled")

        if not authorized():
            return RuntimeResult("failed", "not_started", "POLICY_DENIED")

        with tempfile.TemporaryDirectory(prefix="anygarden-codex-") as directory:
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
                # Cancellation must not lose a child created between fork and return.
                proc = await task
            except OSError:
                return RuntimeResult("failed", "not_started", "ENGINE_ERROR")
            tree = ProcessTree(proc.pid)
            measured: dict[str, int] = {}
            stream: asyncio.Task | None = None
            result = RuntimeResult("unknown", "unknown", "runtime_error")
            try:
                launched(proc.pid)
                if cancelled_at_spawn or not authorized():
                    raise asyncio.CancelledError
                async def collect():
                    token = _MEASURED_USAGE.set(measured)
                    try:
                        return await self._collect(
                            proc, invocation, session_handle, output, emit, authorized
                        )
                    finally:
                        _MEASURED_USAGE.reset(token)

                stream = asyncio.create_task(collect())
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
                # Also reap lingering children on normal CLI exit. A result cannot
                # be terminal while its tool process remains alive.
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
                    # Cancellation during normal-exit cleanup must not interrupt
                    # tree reaping or turn the pending request into success.
                    confirmed = await cleanup_task
                    result = RuntimeResult("cancelled", "stopped", "cancelled")
            if not confirmed:
                return RuntimeResult("unknown", "unknown", "termination_unconfirmed", usage=dict(measured) or None)
            return replace(result, usage=dict(measured) or result.usage)

    async def _collect(
        self, proc, invocation, session, output, emit, authorized
    ) -> RuntimeResult:
        prompt = (
            f"{invocation.instructions}\n\n{invocation.prompt}"
            if invocation.instructions
            else invocation.prompt
        )
        # This coroutine may have been scheduled before a revoke/cancel. Check
        # immediately at the input delivery boundary, with no await before write.
        if not authorized():
            return RuntimeResult("failed", "stopped", "POLICY_DENIED")
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        texts: list[str] = []
        text_size = 0
        completed = failed = False
        failure_count = 0
        failure_code: str | None = None
        measured = _MEASURED_USAGE.get()
        usage = measured if measured is not None else {}
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "thread.started":
                handle = event.get("thread_id")
                if isinstance(handle, str) and 0 < len(handle) <= 256:
                    session = handle
            elif kind == "turn.completed":
                completed = True
                raw = event.get("usage")
                if isinstance(raw, dict):
                    usage.update({
                        k: v
                        for k, v in raw.items()
                        if k in {"input_tokens", "output_tokens", "cached_input_tokens"}
                        and type(v) is int
                        and v >= 0
                    })
            elif kind == "turn.failed":
                failed = True
                failure_count += 1
                if failure_count == 1:
                    error = event.get("error")
                    failure_code = classify_failure(
                        ENGINE, error.get("message") if isinstance(error, dict) else None
                    )
                else:
                    failure_code = None
            elif kind in {
                "turn.started",
                "item.started",
                "item.updated",
                "item.completed",
            }:
                item = event.get("item")
                item_type = item.get("type") if isinstance(item, dict) else None
                if kind == "item.completed" and item_type == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str):
                        text_size += len(text.encode())
                        if text_size > MAX_TEXT:
                            raise ValueError("runtime output exceeds limit")
                        texts.append(text)
                # Progress is deliberately selected metadata, not commands, paths,
                # credentials, native session IDs or arbitrary runtime payloads.
                payload = {"event": kind}
                if item_type in {
                    "agent_message",
                    "command_execution",
                    "file_change",
                    "mcp_tool_call",
                    "plan",
                }:
                    payload["item_type"] = item_type
                emit("progress", payload)
        await proc.wait()
        if proc.returncode != 0:
            # Even an apparently missing session might already have tool effects.
            # No automatic fresh attempt, no transient retry signal.
            return RuntimeResult(
                "failed" if failed else "unknown",
                "stopped",
                (failure_code or "ENGINE_ERROR") if failed else "nonzero_exit",
            )
        if failed:
            return RuntimeResult("failed", "stopped", failure_code or "ENGINE_ERROR")
        if not completed:
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
        return RuntimeResult("succeeded", "finished", "completed", text, session, usage)
