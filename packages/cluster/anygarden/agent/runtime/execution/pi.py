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
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

import structlog

from .codex import ProcessTree
from .contracts import (
    Capabilities,
    Invocation,
    RuntimeResult,
    unsupported_version_detail,
)
from .endpoint import materialize_pi_endpoint, validate_endpoint_invocation
from .failure_feedback import classify_failure
from .lines import MAX_LINE, engine_lines, log_invalid_output
from .pi_auth import INPUT_KEY as PI_INPUT_KEY
from .pi_auth import materialize_native_auth
from .pi_self_tools import ALLOWED_TOOL_NAMES as PI_SELF_TOOL_NAMES
from .turn_clock import TurnClock

logger = structlog.get_logger(__name__)

_MEASURED_USAGE: ContextVar[dict | None] = ContextVar("measured_usage", default=None)
# Whether this turn may have caused a side effect; shared with the timeout
# path, which reports a transient TIMEOUT_STOPPED only when it surely did not.
_SIDE_EFFECTS: ContextVar[dict | None] = ContextVar("pi_side_effects", default=None)
# #892 — the turn's idle clock; every stdout line from the CLI is activity.
_TURN_CLOCK: ContextVar[TurnClock | None] = ContextVar("pi_turn_clock", default=None)
# Fail closed (EXE-10): a timeout is transient only if *every* event of the
# turn is one of these known side-effect-free Pi 0.85.1 event types. Any other
# type -- tool_execution_*, or one a newer Pi adds -- may stand for work with
# effects, so it makes the timeout non-transient.
# The Pi version these lists were reviewed against. A test (EXE-10) fails
# when the pinned engine (ENGINE_VERSION / the machine's managed pin) moves
# off it: re-check the new Pi's event types and message shapes, update the
# lists below, then bump this constant.
PI_SAFE_EVENTS_REVIEWED_FOR = "0.85.1"
_SAFE_EVENTS = frozenset({
    "session", "agent_start", "agent_end", "agent_settled",
    "turn_start", "turn_end",
})
_MESSAGE_EVENTS = frozenset({"message_start", "message_update", "message_end"})
# A message is side-effect free only from these roles with these blocks;
# a ``toolCall`` block, a ``toolResult`` message or anything unknown is not.
_SAFE_MESSAGE_ROLES = frozenset({"user", "assistant"})
_SAFE_CONTENT_BLOCKS = frozenset({"text", "thinking"})


# #880 — Pi's built-in tools by the kind of work they do. The tool name never
# leaves the runtime; only the closed kind does.
_PI_TOOL_ACTIVITY = {
    "bash": "command",
    "edit": "file_change",
    "write": "file_change",
    "read": "file_read",
    "grep": "file_read",
    "find": "file_read",
    "ls": "file_read",
}


def _tool_activity(name: object) -> str:
    if name in PI_SELF_TOOL_NAMES:
        return "anygarden_tool"
    return _PI_TOOL_ACTIVITY.get(name, "tool") if isinstance(name, str) else "tool"


def _may_have_side_effect(kind: object, event: dict) -> bool:
    """Whether this event is not provably side-effect free."""
    if kind in _SAFE_EVENTS:
        return False
    if kind not in _MESSAGE_EVENTS:
        return True
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") not in _SAFE_MESSAGE_ROLES:
        return True
    content = message.get("content")
    if content is None or isinstance(content, str):
        return False
    if not isinstance(content, list):
        return True
    return not all(
        isinstance(block, dict) and block.get("type") in _SAFE_CONTENT_BLOCKS
        for block in content
    )

MAX_TEXT = 1_048_576
ENGINE = "pi-cli"
ENGINE_VERSION = "0.85.1"
# Exact versions the adapter was verified against (event stream, isolation
# flags, models.json schema, PI_* env names). Never prefix-match.
SUPPORTED_VERSIONS = (ENGINE_VERSION,)


class PiRuntime:
    def __init__(self, executable: Path):
        if not executable.is_absolute():
            raise ValueError("Pi executable must be an absolute local path")
        self.executable = executable
        # Last ``--version`` output seen by the gate (non-secret), used to
        # explain an UNSUPPORTED_RUNTIME failure to the operator (#687).
        self.observed_version: str | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            engine=ENGINE, engine_version=ENGINE_VERSION, cancel=os.name == "posix"
        )

    def command(
        self, invocation: Invocation, session: str | None, output: Path
    ) -> list[str]:
        """pi --print --mode json with the minimal ambient surface.

        The prompt is delivered on stdin (never argv) so option-looking prompt
        text cannot be parsed as flags. Resume uses ``--session <handle>`` with
        the previous native session id (``--resume`` is a bare flag that opens
        Pi's interactive session selector and never settles in JSON mode, see
        #714); the session directory is pinned into
        the sandboxed home so a resumed handle resolves inside the isolated
        workspace. ``--api-key`` is deliberately never used: provider
        credentials travel only via the caller-staged environment.
        ``invocation.provider`` is required by the contract for pi-cli and is
        mapped to ``--provider``; validate() already rejects None.
        """
        validate_endpoint_invocation(invocation)
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
        if invocation.permission_level == "restricted":
            cmd += ["--tools", "read,grep,find,ls"]
        if invocation.model:
            cmd += ["--model", invocation.model]
        if session:
            cmd += ["--session", session]
        return cmd

    @staticmethod
    def environment(invocation: Invocation) -> dict[str, str]:
        # Never inherit the node's ambient environment or home/auth/config.
        endpoint = validate_endpoint_invocation(invocation)
        native_auth = getattr(invocation, "native_auth", None)
        key = invocation.environment.get(PI_INPUT_KEY)
        if bool(native_auth) != bool(key) or (
            native_auth is not None
            and (
                invocation.scope.engine != ENGINE
                or endpoint is not None
                or native_auth.provider != invocation.provider
            )
        ):
            raise ValueError("Pi provider credential does not match invocation")
        materialize_native_auth(invocation.runtime_home, native_auth, key)
        materialize_pi_endpoint(invocation.runtime_home, endpoint)
        env = dict(invocation.environment)
        env.pop(PI_INPUT_KEY, None)
        for key in env:
            if key.startswith(("ANYGARDEN_", "RAFT_", "SLOCK_")):
                raise ValueError(
                    "server credentials are not runtime environment inputs"
                )
        home = str(invocation.runtime_home)
        env["HOME"] = home
        # Env split (architect + PM review 2026-09-22):
        # - PI_PACKAGE_DIR is an INSTALL asset path. Remove the ambient
        #   override entirely and let the CLI resolve its own executable —
        #   pinning it at the sandbox home made ``--version`` read a missing
        #   package.json (0.0.0) and fail the gate.
        # - PI_CODING_AGENT_DIR is the USER config parent (models.json /
        #   auth.json / settings): pin it at an isolated adapter-owned
        #   directory under the sandbox home, never at the install dir
        #   (that would break isolation).
        # - PI_CODING_AGENT_SESSION_DIR is the isolated session dir.
        # - PI_CONFIG_DIR / PI_SESSION_DIR are NOT keys this install reads;
        #   historical names are dropped so they cannot alias anything.
        env.pop("PI_PACKAGE_DIR", None)
        env.pop("PI_CONFIG_DIR", None)
        env.pop("PI_SESSION_DIR", None)
        env.pop("PI_CODING_AGENT_DIR", None)
        agent_dir = invocation.runtime_home / ".pi" / "agent"
        env["PI_CODING_AGENT_DIR"] = str(agent_dir)
        env["PI_CODING_AGENT_SESSION_DIR"] = str(invocation.runtime_home / "sessions")
        return env

    def unsupported_detail(self) -> str:
        return unsupported_version_detail(
            ENGINE, self.observed_version, SUPPORTED_VERSIONS
        )

    async def check_native_auth(
        self, invocation: Invocation, env: dict[str, str]
    ) -> str | None:
        """Use Pi's own local auth/model resolver in the runtime environment."""
        if invocation.native_auth is None:
            return None
        command = [
            str(self.executable),
            "auth",
            "check",
            "--provider",
            invocation.provider,
        ]
        if invocation.model:
            command += ["--model", invocation.model]
        command += ["--json", "--no-refresh"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=invocation.workspace,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            if len(stdout) > 4096:
                return "AUTH_CHECK_FAILED"
            result = json.loads(stdout)
            if (
                not isinstance(result, dict)
                or result.get("provider") != invocation.provider
            ):
                return "AUTH_CHECK_FAILED"
            if result.get("status") == "ready" and proc.returncode == 0:
                return None
            if result.get("reason") == "provider_not_found":
                return "UNKNOWN_PROVIDER"
            if result.get("reason") in {
                "credentials_not_configured",
                "credential_not_available",
            }:
                return "AUTH_MISSING"
            return "AUTH_CHECK_FAILED"
        except (OSError, TimeoutError, ValueError, UnicodeDecodeError):
            return "AUTH_CHECK_FAILED"

    async def preflight(self, invocation: Invocation) -> str | None:
        try:
            env = self.environment(invocation)
        except (OSError, ValueError):
            return "AUTH_CHECK_FAILED"
        try:
            if not await self._version_matches(invocation, env):
                return "UNSUPPORTED_RUNTIME"
        except (OSError, TimeoutError):
            return "UNSUPPORTED_RUNTIME"
        return await self.check_native_auth(invocation, env)

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
            # The CLI prints the package.json version of PI_PACKAGE_DIR —
            # observed bare (``0.85.1``), sometimes with a ``pi `` prefix.
            # Parse to a version tuple and compare EXACTLY: a substring
            # containment check would admit unsupported ``0.85.10``.
            out = stdout.decode(errors="replace").strip().removeprefix("pi ").strip()
            self.observed_version = out[:64] or None
            try:
                observed = tuple(int(part) for part in out.split("."))
                supported = {
                    tuple(int(part) for part in version.split("."))
                    for version in SUPPORTED_VERSIONS
                }
            except ValueError:
                return False
            return proc.returncode == 0 and observed in supported
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
        try:
            env = self.environment(invocation)
        except (OSError, ValueError):
            if invocation.native_auth is None:
                raise
            return RuntimeResult("failed", "not_started", "AUTH_CHECK_FAILED")
        try:
            if not await self._version_matches(invocation, env):
                return RuntimeResult("failed", "not_started", "UNSUPPORTED_RUNTIME")
        except asyncio.CancelledError:
            return RuntimeResult("cancelled", "not_started", "cancelled")
        except (TimeoutError, OSError):
            return RuntimeResult("failed", "not_started", "UNSUPPORTED_RUNTIME")

        if not authorized():
            return RuntimeResult("failed", "not_started", "POLICY_DENIED")

        auth_error = await self.check_native_auth(invocation, env)
        if auth_error:
            return RuntimeResult("failed", "not_started", auth_error)

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
            measured: dict[str, int] = {}
            effects = {"started": False}
            stream: asyncio.Task | None = None
            # #892 — ``timeout_seconds`` is an idle limit, not a turn cap.
            clock = TurnClock(
                invocation.timeout_seconds, asyncio.get_running_loop().time
            )
            emit = clock.wrap(emit)
            result = RuntimeResult("unknown", "unknown", "runtime_error")
            try:
                launched(proc.pid)
                if cancelled_at_spawn or not authorized():
                    raise asyncio.CancelledError

                async def collect():
                    token = _MEASURED_USAGE.set(measured)
                    effects_token = _SIDE_EFFECTS.set(effects)
                    clock_token = _TURN_CLOCK.set(clock)
                    try:
                        return await self._collect(
                            proc, invocation, session_handle, output, emit, authorized
                        )
                    finally:
                        _TURN_CLOCK.reset(clock_token)
                        _SIDE_EFFECTS.reset(effects_token)
                        _MEASURED_USAGE.reset(token)

                stream = asyncio.create_task(collect())
                while not stream.done():
                    tree.observe()
                    remaining = clock.remaining()
                    if remaining <= 0:
                        logger.warning(
                            "execution.idle_timeout",
                            engine=ENGINE,
                            idle=invocation.timeout_seconds,
                            elapsed=round(clock.elapsed(), 1),
                            open_tools=clock.open_tools,
                        )
                        raise TimeoutError
                    await asyncio.wait({stream}, timeout=min(0.05, remaining))
                result = stream.result()
            except TimeoutError:
                result = RuntimeResult("failed", "stopped", (
                    "TIMEOUT_AFTER_SIDE_EFFECT" if effects["started"]
                    else "TIMEOUT_STOPPED"))
            except asyncio.CancelledError:
                result = RuntimeResult("cancelled", "stopped", "cancelled")
            except Exception as exc:  # noqa: BLE001 — reap child before reporting unknown
                log_invalid_output(ENGINE, exc)
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
                return RuntimeResult(
                    "unknown",
                    "unknown",
                    "termination_unconfirmed",
                    usage=dict(measured) or None,
                )
            return replace(result, usage=dict(measured) or result.usage)

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
        # #880 — text in a message that calls tools is narration; the text
        # after the last tool work is the reply.
        reply: list[str] = []
        narration: str | None = None
        text_size = 0
        measured = _MEASURED_USAGE.get()
        usage = measured if measured is not None else {}
        effects = _SIDE_EFFECTS.get()
        if effects is None:
            effects = {"started": False}
        session_handle = session
        failed = False
        failure_count = 0
        failure_code: str | None = None
        settled = False
        clock = _TURN_CLOCK.get()
        async for line in engine_lines(proc.stdout, engine=ENGINE):
            if clock is not None:
                clock.touch()
            if line is None:
                effects["started"] = True
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                # Unrecognised output: fail closed for timeout classification.
                effects["started"] = True
                continue
            if not isinstance(event, dict):
                effects["started"] = True
                continue
            kind = event.get("type")
            if _may_have_side_effect(kind, event):
                effects["started"] = True
            if kind == "session":
                handle = event.get("id")
                if isinstance(handle, str) and 0 < len(handle) <= 256:
                    session_handle = handle
            elif kind == "message_end":
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") != "assistant":
                    continue
                raw_usage = message.get("usage")
                if isinstance(raw_usage, dict):
                    for source, target in (
                        ("input", "input_tokens"),
                        ("output", "output_tokens"),
                    ):
                        value = raw_usage.get(source)
                        if type(value) is int and value >= 0:
                            usage[target] = usage.get(target, 0) + value
                content = message.get("content")
                if isinstance(content, list):
                    texts: list[str] = []
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
                    if any(
                        isinstance(block, dict) and block.get("type") == "toolCall"
                        for block in content
                    ):
                        for text in [*reply, *texts]:
                            if text.strip():
                                narration = text
                                emit("progress", {"event": "narration", "text": text})
                        reply = []
                    else:
                        reply.extend(texts)
                if message.get("stopReason") == "error":
                    failed = True
                    failure_count += 1
                    if failure_count == 1:
                        failure_code = classify_failure(ENGINE, message.get("errorMessage"))
                    else:
                        failure_code = None
                    # Raw provider errors never leave this boundary.
            elif kind == "agent_end":
                if event.get("willRetry") is True:
                    continue
            elif kind == "agent_settled":
                settled = True
            elif kind in {"tool_execution_start", "tool_execution_end"}:
                activity = _tool_activity(event.get("toolName"))
                if kind == "tool_execution_start":
                    emit("progress", {
                        "event": "activity", "activity": activity, "phase": "started",
                    })
                elif not (activity == "file_change" and event.get("isError") is True):
                    # A failed edit changed nothing; other failed tools still ran.
                    emit("progress", {
                        "event": "activity", "activity": activity, "phase": "completed",
                        "count": 1,
                    })
                elif clock is not None:
                    # The failed edit still ended for the idle clock (#892).
                    clock.tool_ended()
            elif kind == "message_update":
                update = event.get("assistantMessageEvent")
                phase = {"thinking_start": "started", "thinking_end": "completed"}.get(
                    update.get("type") if isinstance(update, dict) else None
                )
                if phase is not None:
                    emit("progress", {
                        "event": "activity", "activity": "thinking", "phase": phase,
                        **({"count": 1} if phase == "completed" else {}),
                    })
            elif kind in {"agent_start", "turn_start", "message_start", "turn_end"}:
                payload = {"event": kind}
                if kind == "message_start":
                    message = event.get("message")
                    if isinstance(message, dict) and message.get("role") == "assistant":
                        payload["role"] = "assistant"
                emit("progress", payload)
        await proc.wait()
        if proc.returncode != 0:
            return RuntimeResult("failed", "stopped", failure_code or "ENGINE_ERROR")
        if failed:
            return RuntimeResult("failed", "stopped", failure_code or "ENGINE_ERROR")
        if not settled:
            return RuntimeResult("unknown", "stopped", "missing_terminal_event")
        # A turn that ended on tool work replies with its last narration.
        text = "\n".join(reply) if any(t.strip() for t in reply) else narration or ""
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
