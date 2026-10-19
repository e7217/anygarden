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
import structlog

from .contracts import Capabilities, Invocation, RuntimeResult
from .endpoint import codex_endpoint_arguments, validate_endpoint_invocation
from .failure_feedback import CodexFailureTracker, stderr_tail
from .usage import SOURCE, TOKEN_KEYS, counters

logger = structlog.get_logger(__name__)

_MEASURED_USAGE: ContextVar[dict | None] = ContextVar("measured_usage", default=None)
# The turn's failure tracker outlives a timed-out collector, so the timeout
# path can tell whether a side-effecting item already started.
_FAILURES: ContextVar[CodexFailureTracker | None] = ContextVar("codex_failures", default=None)
# Bounded stderr tail of the running CLI — local diagnosis only.
_STDERR_TAIL: ContextVar[bytearray | None] = ContextVar("stderr_tail", default=None)
_STDERR_KEEP = 2048

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


def _activity_kind(item: object) -> str | None:
    """Closed #880 activity kind of a Codex item; never its command or path."""
    if not isinstance(item, dict):
        return None
    item_type = item.get("type")
    if item_type == "command_execution":
        return "command"
    if item_type == "file_change":
        return "file_change"
    if item_type == "web_search":
        return "web_search"
    if item_type == "mcp_tool_call":
        return "anygarden_tool" if item.get("server") == "anygarden" else "tool"
    return None


def _emit_activity(emit, kind: str, activity: str, item: dict) -> None:
    if kind == "item.started":
        emit("progress", {"event": "activity", "activity": activity, "phase": "started"})
        return
    if activity == "file_change":
        # A failed patch changed nothing; a completed one counts its files.
        if item.get("status") == "failed":
            return
        changes = item.get("changes")
        count = len(changes) if isinstance(changes, list) and changes else 1
    else:
        count = 1
    emit("progress", {
        "event": "activity", "activity": activity, "phase": "completed", "count": count,
    })


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
                    stderr=asyncio.subprocess.PIPE,
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
            measured: dict = {}
            stream: asyncio.Task | None = None
            errors = bytearray()

            async def drain_stderr() -> None:
                # Always drained (a full pipe would block the CLI); only the
                # tail is kept for the local nonzero-exit log.
                while chunk := await proc.stderr.read(4096):
                    errors.extend(chunk)
                    del errors[:-_STDERR_KEEP]

            stderr_task = asyncio.create_task(drain_stderr())
            failures = CodexFailureTracker()
            result = RuntimeResult("unknown", "unknown", "runtime_error")
            try:
                launched(proc.pid)
                if cancelled_at_spawn or not authorized():
                    raise asyncio.CancelledError
                async def collect():
                    token = _MEASURED_USAGE.set(measured)
                    tail_token = _STDERR_TAIL.set(errors)
                    failures_token = _FAILURES.set(failures)
                    try:
                        return await self._collect(
                            proc, invocation, session_handle, output, emit, authorized
                        )
                    finally:
                        _FAILURES.reset(failures_token)
                        _STDERR_TAIL.reset(tail_token)
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
                # Transient only if no side-effecting item started (same rule
                # as CodexFailureTracker.code()); otherwise never retried.
                result = RuntimeResult("failed", "stopped", (
                    "TIMEOUT_AFTER_SIDE_EFFECT" if failures.side_effect_started
                    else "TIMEOUT_STOPPED"))
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
                    stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
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
                result = RuntimeResult("unknown", "unknown", "termination_unconfirmed")
            return replace(
                result,
                # Only the store owns a durable same-handle baseline. Raw
                # provider totals never masquerade as invocation usage here.
                usage=None,
                usage_source=SOURCE,
                usage_cumulative=measured.get("provider_cumulative"),
                usage_session_handle=measured.get("observed_session_handle"),
                usage_terminal=bool(measured.get("terminal_usage")),
            )

    @staticmethod
    def _log_failure(exit_code, failures: CodexFailureTracker, code: str | None) -> None:
        """Local agent log only: redacted reason + stderr tail for diagnosis."""
        errors = _STDERR_TAIL.get()
        logger.error(
            "codex_cli.nonzero_exit" if exit_code else "codex_cli.turn_failed",
            exit_code=exit_code,
            status=failures.status,
            reason=failures.reason,
            code=code or "ENGINE_ERROR",
            transient=code == "MODEL_TRANSIENT_FAILURE",
            side_effect_started=failures.side_effect_started,
            stderr_tail=stderr_tail(bytes(errors)) if errors else "",
        )

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
        # #880 — a message is narration once more work follows it; the last
        # one is the reply (``-o`` writes exactly that message).
        unconfirmed: str | None = None
        completed = failed = False
        failures = _FAILURES.get() or CodexFailureTracker()
        measured = _MEASURED_USAGE.get()
        usage = measured if measured is not None else {}
        while line := await proc.stdout.readline():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            failures.observe(event)
            kind = event.get("type")
            if kind == "thread.started":
                handle = event.get("thread_id")
                if isinstance(handle, str) and 0 < len(handle) <= 256:
                    session = handle
                    usage["observed_session_handle"] = handle
            elif kind == "turn.completed":
                completed = True
                raw = event.get("usage")
                usage["terminal_usage"] = raw is not None
                usage["provider_cumulative"] = counters(raw)
                for key in TOKEN_KEYS:
                    usage.pop(key, None)
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
                usage["terminal_usage"] = False
            elif kind in {
                "turn.started",
                "item.started",
                "item.updated",
                "item.completed",
            }:
                if kind == "turn.started":
                    usage["terminal_usage"] = False
                item = event.get("item")
                item_type = item.get("type") if isinstance(item, dict) else None
                activity = _activity_kind(item) if kind != "item.updated" else None
                if activity is not None and unconfirmed:
                    emit("progress", {"event": "narration", "text": unconfirmed})
                    unconfirmed = None
                if kind == "item.completed" and item_type == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str):
                        text_size += len(text.encode())
                        if text_size > MAX_TEXT:
                            raise ValueError("runtime output exceeds limit")
                        texts.append(text)
                        if unconfirmed:
                            emit("progress", {"event": "narration", "text": unconfirmed})
                        unconfirmed = text if text.strip() else None
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
                if activity is not None:
                    _emit_activity(emit, kind, activity, item)
        await proc.wait()
        # Transient (retryable) only when the provider failure looks transient
        # AND no side-effecting item started in this turn; see
        # CodexFailureTracker. The raw message never leaves this process.
        failure_code = failures.code()
        if proc.returncode != 0 or failed:
            self._log_failure(proc.returncode, failures, failure_code)
        if proc.returncode != 0:
            if failed:
                return RuntimeResult("failed", "stopped", failure_code or "ENGINE_ERROR")
            if failure_code == "MODEL_TRANSIENT_FAILURE":
                # Exited on a transient provider error before any tool item.
                return RuntimeResult("failed", "stopped", failure_code)
            # Even an apparently missing session might already have tool effects.
            # No automatic fresh attempt, no transient retry signal.
            return RuntimeResult("unknown", "stopped", "nonzero_exit")
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
        return RuntimeResult(
            "succeeded", "finished", "completed", text, session,
            {key: usage[key] for key in TOKEN_KEYS if key in usage} or None,
        )
