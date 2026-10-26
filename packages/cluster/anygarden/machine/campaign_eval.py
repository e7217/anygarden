"""Campaign evaluation runner on the machine (#930 §3.7).

The server asks the machine that hosts a campaign's agent to run the
campaign's evaluator command in that agent's workspace and report the
score. The score is never taken from the agent itself (plan decision 2).

Contract of an evaluator command:

- it runs with ``/bin/sh -c <command>`` in ``<agent workspace>/<cwd>``,
- it exits 0 and prints, as its **last non-empty stdout line**, one JSON
  object with the numeric ``score_key`` value and a boolean ``valid``
  (whether the guardrails passed — the script decides),
- it finishes within ``timeout_sec``; otherwise its whole process group is
  killed and the job reports ``timed_out``.

Before running, the runner hashes the ``pin_paths`` (files or directory
trees) and compares them with ``expected_pins``. A mismatch reports
``invalid`` and does not run the command.

Safety limits (not isolation — that is #931):

- ``cwd`` and every pin path must resolve inside the agent's workspace;
  ``..`` and symlinks that leave it are refused.
- The command gets a minimal environment built from an allowlist
  (``PATH``, ``HOME``, locale, temp dir). The agent token, MCP token and
  engine credentials are never passed, and nothing else is inherited.
- One evaluation runs per machine at a time. A request that arrives while
  another job runs is **refused as busy**; the server keeps the attempt
  pending and asks again on its next tick. A queue would let a job wait
  behind a six-hour evaluation and miss its server-side deadline, while a
  refusal starts the deadline only once the job actually runs.
- A repeated request for the job that is running is acknowledged again; a
  repeated request for a finished job re-sends its cached result (the
  first one may have been lost on a disconnect).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import signal
import sys
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from anygarden.machine.agent_dir import AgentFilePathError, validate_agent_id

log = structlog.get_logger(__name__)

TAIL_CHARS = 4096
"""Characters of stdout/stderr kept in the result."""
PARSE_WINDOW_BYTES = 64 * 1024
"""Bytes of stdout kept to find the last line (a long final JSON line)."""
KILL_GRACE_SECONDS = 2.0
MAX_PIN_FILES = 10_000
RESULT_CACHE_SIZE = 16
MAX_PARSED_KEYS = 50
MAX_PARSED_STRING = 200

ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "TMPDIR", "TEMP", "TMP",
    # Windows needs these to start processes at all.
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR",
)

STATUSES = frozenset({"ok", "failed", "invalid", "timed_out"})


class EvalRequestError(ValueError):
    """The request cannot be run; ``code`` goes back as the result error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EvalJob:
    job_id: str
    agent_id: str
    command: str
    timeout_sec: int
    cwd: str = "."
    pin_paths: tuple[str, ...] = ()
    expected_pins: Mapping[str, str] | None = None
    score_key: str = "score"


@dataclass
class EvalResult:
    job_id: str
    agent_id: str
    status: str
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    parsed: dict[str, Any] | None = None
    pins: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def frame(self) -> dict[str, Any]:
        return {
            "type": "campaign_eval_result",
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "parsed": self.parsed,
            "pins": self.pins,
            "error": self.error,
        }


# ── Paths ───────────────────────────────────────────────────────────


def workspace_root(agents_root: Path, agent_id: str) -> Path:
    """The agent's workspace (its process cwd), resolved.

    The workspace itself must be a real directory, not a symlink.
    """
    try:
        validate_agent_id(agent_id)
    except AgentFilePathError as exc:
        raise EvalRequestError("invalid_agent_id") from exc
    root = Path(agents_root) / agent_id
    if root.is_symlink() or not root.is_dir():
        raise EvalRequestError("workspace_missing")
    return root.resolve()


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve *relative* under *root*, following symlinks, and refuse any
    result outside *root* (``..``, absolute paths, escaping symlinks)."""
    if not isinstance(relative, str) or "\x00" in relative:
        raise EvalRequestError("path_escape")
    candidate = Path(relative.replace("\\", "/"))
    if candidate.is_absolute():
        raise EvalRequestError("path_escape")
    resolved = (root / candidate).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise EvalRequestError("path_escape")
    return resolved


# ── Pins ────────────────────────────────────────────────────────────


def _hash_file(path: Path, digest: Any) -> None:
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)


def pin_digest(root: Path, relative: str) -> str:
    """sha256 of a pinned file, or of a directory tree (sorted relative
    names and contents). A symlink inside a pinned tree that leaves the
    workspace is refused."""
    target = resolve_inside(root, relative)
    if target.is_file():
        digest = hashlib.sha256()
        _hash_file(target, digest)
        return digest.hexdigest()
    if not target.is_dir():
        raise EvalRequestError("pin_missing")
    digest = hashlib.sha256()
    count = 0
    for dirpath, dirnames, filenames in os.walk(target):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise EvalRequestError("path_escape")
            count += 1
            if count > MAX_PIN_FILES:
                raise EvalRequestError("pin_too_large")
            digest.update(path.relative_to(target).as_posix().encode() + b"\0")
            _hash_file(resolved, digest)
            digest.update(b"\0")
    return digest.hexdigest()


def compute_pins(root: Path, pin_paths: tuple[str, ...] | list[str]) -> dict[str, str]:
    return {path: pin_digest(root, path) for path in pin_paths}


# ── Output parsing ──────────────────────────────────────────────────


def _bounded(parsed: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in parsed.items():
        if len(kept) >= MAX_PARSED_KEYS or not isinstance(key, str):
            break
        if isinstance(value, bool | int | float) or value is None:
            kept[key[:MAX_PARSED_STRING]] = value
        elif isinstance(value, str):
            kept[key[:MAX_PARSED_STRING]] = value[:MAX_PARSED_STRING]
    return kept


def parse_score_line(stdout: str, score_key: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse the last non-empty stdout line. Returns ``(parsed, None)`` or
    ``(None, error_code)``."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None, "no_output"
    try:
        value = json.loads(lines[-1])
    except ValueError:
        return None, "last_line_not_json"
    if not isinstance(value, dict):
        return None, "last_line_not_object"
    score = value.get(score_key)
    if (
        isinstance(score, bool)
        or not isinstance(score, int | float)
        or not math.isfinite(score)
    ):
        return None, "score_missing"
    if not isinstance(value.get("valid"), bool):
        return None, "valid_missing"
    parsed = _bounded(value)
    parsed[score_key] = float(score)
    parsed["valid"] = value["valid"]
    return parsed, None


# ── Environment ─────────────────────────────────────────────────────


def eval_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Minimal env for the evaluator: allowlisted keys only."""
    source = os.environ if base is None else base
    env = {key: source[key] for key in ENV_ALLOWLIST if key in source}
    env.setdefault("PATH", os.defpath)
    env["ANYGARDEN_CAMPAIGN_EVAL"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ── Running ─────────────────────────────────────────────────────────


def _tail(data: bytes | bytearray, limit: int = TAIL_CHARS) -> str:
    return bytes(data).decode("utf-8", errors="replace")[-limit:]


async def _drain(stream: asyncio.StreamReader | None, buffer: bytearray) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(1 << 16)
        if not chunk:
            return
        buffer.extend(chunk)
        if len(buffer) > PARSE_WINDOW_BYTES:
            del buffer[: len(buffer) - PARSE_WINDOW_BYTES]


def _kill_group(proc: asyncio.subprocess.Process, sig: int) -> None:
    if sys.platform == "win32":  # pragma: no cover — POSIX-first feature
        from anygarden.machine.proc_kill import terminate_tree

        terminate_tree(proc.pid, timeout=KILL_GRACE_SECONDS)
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def run_eval(
    agents_root: Path,
    job: EvalJob,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> EvalResult:
    """Check pins, run the command and parse its score. Never raises for a
    bad request or a failing command; the result says what happened."""
    result = EvalResult(job_id=job.job_id, agent_id=job.agent_id, status="failed")
    try:
        root = workspace_root(agents_root, job.agent_id)
        cwd = resolve_inside(root, job.cwd or ".")
        if not cwd.is_dir():
            raise EvalRequestError("cwd_missing")
        result.pins = compute_pins(root, job.pin_paths)
    except EvalRequestError as exc:
        result.error = exc.code
        return result
    except OSError:
        result.error = "pin_unreadable"
        return result
    if job.expected_pins is not None and dict(job.expected_pins) != result.pins:
        result.status = "invalid"
        result.error = "pin_mismatch"
        return result

    stdout = bytearray()
    stderr = bytearray()
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", job.command,
            cwd=str(cwd),
            env=eval_environment(base_environment),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        result.error = "spawn_failed"
        return result

    readers = asyncio.gather(_drain(proc.stdout, stdout), _drain(proc.stderr, stderr))
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=job.timeout_sec)
    except TimeoutError:
        timed_out = True
        _kill_group(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=KILL_GRACE_SECONDS)
        except TimeoutError:
            pass
        # SIGKILL the group regardless: children that ignored SIGTERM, or
        # that outlived the shell, must not keep running.
        _kill_group(proc, signal.SIGKILL)
        await proc.wait()
    except asyncio.CancelledError:
        # Daemon shutdown: never leave the evaluator running.
        _kill_group(proc, signal.SIGKILL)
        raise
    finally:
        try:
            # A grandchild that left the group could hold the pipes open.
            await asyncio.wait_for(readers, timeout=KILL_GRACE_SECONDS)
        except TimeoutError:
            readers.cancel()
        except OSError:  # pragma: no cover — defensive
            log.warning("campaign_eval.read_failed", job_id=job.job_id)
    if not timed_out:
        # The shell may exit while its children still run in the group.
        _kill_group(proc, signal.SIGKILL)

    result.exit_code = proc.returncode
    result.stdout_tail = _tail(stdout)
    result.stderr_tail = _tail(stderr)
    if timed_out:
        result.status = "timed_out"
        result.error = "timeout"
        return result
    if proc.returncode != 0:
        result.error = "exit_code"
        return result
    parsed, error = parse_score_line(_tail(stdout, PARSE_WINDOW_BYTES), job.score_key)
    if parsed is None:
        result.error = error
        return result
    result.status = "ok"
    result.parsed = parsed
    return result


SendFrame = Callable[[dict[str, Any]], Awaitable[None]]


class CampaignEvalRunner:
    """One evaluation at a time per machine; see the module docstring."""

    def __init__(
        self,
        agents_root: Path,
        *,
        base_environment: Mapping[str, str] | None = None,
        runner: Callable[..., Awaitable[EvalResult]] = run_eval,
    ) -> None:
        self._agents_root = Path(agents_root)
        self._base_environment = base_environment
        self._runner = runner
        self._current: tuple[str, asyncio.Task[None]] | None = None
        self._results: OrderedDict[str, EvalResult] = OrderedDict()

    @property
    def running_job_id(self) -> str | None:
        if self._current is None or self._current[1].done():
            return None
        return self._current[0]

    async def submit(self, job: EvalJob, send: SendFrame) -> tuple[bool, str | None]:
        """Accept or refuse *job*. Returns ``(accepted, reason)``; the
        result is sent with *send* when the job finishes."""
        cached = self._results.get(job.job_id)
        if cached is not None:
            await send(cached.frame())
            return True, "already_finished"
        running = self.running_job_id
        if running == job.job_id:
            return True, "already_running"
        if running is not None:
            return False, "busy"
        task = asyncio.create_task(self._run(job, send), name=f"campaign-eval-{job.job_id}")
        self._current = (job.job_id, task)
        return True, None

    async def _run(self, job: EvalJob, send: SendFrame) -> None:
        try:
            result = await self._runner(
                self._agents_root, job, base_environment=self._base_environment
            )
        except Exception:  # pragma: no cover — defensive boundary
            log.exception("campaign_eval.crashed", job_id=job.job_id)
            result = EvalResult(
                job_id=job.job_id, agent_id=job.agent_id, status="failed",
                error="runner_crashed",
            )
        self._results[job.job_id] = result
        while len(self._results) > RESULT_CACHE_SIZE:
            self._results.popitem(last=False)
        log.info("campaign_eval.finished", job_id=job.job_id, status=result.status,
                 error=result.error)
        await send(result.frame())

    async def close(self) -> None:
        if self._current is not None and not self._current[1].done():
            self._current[1].cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._current[1]


__all__ = [
    "ENV_ALLOWLIST",
    "STATUSES",
    "CampaignEvalRunner",
    "EvalJob",
    "EvalRequestError",
    "EvalResult",
    "compute_pins",
    "eval_environment",
    "parse_score_line",
    "pin_digest",
    "resolve_inside",
    "run_eval",
    "workspace_root",
]
