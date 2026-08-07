"""Fail-closed release smoke for one protected Codex canary invocation.

The default outcome is ``BLOCKED_CONFIGURATION``.  A live call is possible
only from the protected workflow, on the exact default-branch SHA, inside the
dedicated isolated container.  Raw prompts, responses, credentials, sessions,
and stderr are never written to evidence or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from ipaddress import AddressValueError, IPv4Address, IPv4Network
from pathlib import Path
from urllib.parse import urlsplit

CANARY_PROMPT = (
    "Reply with exactly ANYGARDEN_SMOKE_OK and nothing else. "
    "Do not use tools, read files, or create, modify, or delete anything."
)
CANARY_RESPONSE = "ANYGARDEN_SMOKE_OK"
MAX_RESPONSE_BYTES = 256
HARD_TIMEOUT_SECONDS = 60
TERM_GRACE_SECONDS = 2
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
ENGINE_VERSION_PATTERN = re.compile(
    r"^codex-cli [0-9]{1,4}(?:\.[0-9]{1,4}){2}(?:[-+][A-Za-z0-9.-]{1,40})?$"
)
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
ALLOWED_ITEM_TYPES = {"agent_message", "reasoning"}
RUNTIME_STATE_ROOT = Path("/tmp")
MAX_FAILURE_EVENT_BYTES = 64 * 1024
MAX_FAILURE_MESSAGE_BYTES = 4 * 1024
FAILURE_CATEGORY_NOT_APPLICABLE = "NOT_APPLICABLE"
FAILURE_CATEGORY_UNKNOWN = "UNKNOWN"
FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT = "ENGINE_EMPTY_OUTPUT"
FAILURE_CATEGORIES = frozenset(
    {
        FAILURE_CATEGORY_NOT_APPLICABLE,
        "AUTH_REJECTED",
        "ENGINE_CONFIG",
        FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT,
        "MODEL_ACCESS",
        "RATE_LIMIT",
        "UPSTREAM",
        FAILURE_CATEGORY_UNKNOWN,
    }
)
FAILURE_PHASES = frozenset(
    {
        "ENGINE_LAUNCH",
        "ENGINE_EXECUTION",
        "ENGINE_EXIT",
        "STDERR_CLASSIFICATION",
        "STDOUT_CLASSIFICATION",
        "RESPONSE_PARSE",
    }
)
ENGINE_EXIT_STATES = frozenset({"ZERO", "NONZERO", "SIGNAL", "NOT_OBSERVED"})
STDOUT_STATES = frozenset(
    {
        "EMPTY",
        "SINGLE_FAILURE_EVENT",
        "TERMINAL_FAILURE",
        "MULTIPLE_FAILURE_EVENTS",
        "MALFORMED",
        "OVERSIZE",
        "NON_FAILURE_OUTPUT",
        "NOT_OBSERVED",
    }
)
STDERR_STATES = frozenset(
    {
        "EMPTY",
        "SINGLE_FAILURE_SIGNAL",
        "MULTIPLE_FAILURE_SIGNALS",
        "MALFORMED",
        "OVERSIZE",
        "UNRECOGNIZED",
        "NOT_OBSERVED",
    }
)
MAX_EVIDENCE_DURATION_MS = 24 * 60 * 60 * 1000
HTTP_STATUS_PATTERN = re.compile(
    r"(?:^|:\s)(?:unexpected status\s+|exceeded retry limit, last status:\s*)"
    r"(?P<status>[1-5][0-9]{2})\b",
    re.IGNORECASE,
)
MODEL_FAILURE_SIGNALS = (
    "model not found",
    "model does not exist",
    "does not exist or you do not have access to it",
    "invalid model",
)
AUTH_FAILURE_SIGNALS = (
    "api key is invalid",
    "authentication failed",
    "incorrect api key",
    "invalid api key",
    "invalid authentication",
    "missing bearer authentication",
)
ENGINE_CONFIG_FAILURE_SIGNALS = (
    "failed to deserialize config",
    "failed to parse configuration",
    "invalid configuration",
    "invalid reasoning effort",
    "invalid value for model_reasoning_effort",
    "invalid value for 'model_reasoning_effort'",
    "unknown configuration key",
    "unknown reasoning effort",
    "unrecognized configuration",
    "unrecognized reasoning effort",
    "unsupported reasoning effort",
)
RATE_LIMIT_FAILURE_SIGNALS = (
    "quota exceeded.",
    "you've hit your usage limit",
    "you hit your spend cap",
    "you've hit your spend cap",
    "workspace is out of credits",
)
UPSTREAM_FAILURE_SIGNALS = (
    "selected model is at capacity.",
    "we're currently experiencing high demand",
    "connection failed:",
    "error while reading the server response:",
    "stream disconnected before completion:",
    "request timed out",
)
RFC1918_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
SMOKE_FAILURE_RESULT_CODES = {
    "approval_requested": "FAIL_APPROVAL_REQUESTED",
    "canary_mismatch": "FAIL_CANARY_MISMATCH",
    "engine_nonzero": "FAIL_ENGINE_NONZERO",
    "protocol_output_limit": "FAIL_PROTOCOL_OUTPUT_LIMIT",
    "protocol_shape": "FAIL_PROTOCOL_SHAPE",
    "response_limit": "FAIL_RESPONSE_LIMIT",
    "tool_requested": "FAIL_TOOL_REQUESTED",
}


@dataclass(frozen=True)
class ExecutionDiagnostics:
    """Fixed, non-sensitive process-boundary observations only."""

    failure_phase: str = "ENGINE_LAUNCH"
    engine_exit_state: str = "NOT_OBSERVED"
    stdout_state: str = "NOT_OBSERVED"
    stderr_state: str = "NOT_OBSERVED"
    setup_ms: int = 0
    engine_ms: int = 0
    classification_ms: int = 0


@dataclass(frozen=True)
class ExecutionResult:
    output: bytes
    engine_version: str
    diagnostics: ExecutionDiagnostics


class BlockedConfiguration(Exception):
    """The protected smoke prerequisites are absent or stale."""


class SmokeFailure(Exception):
    """The engine ran but did not satisfy the fixed canary contract."""

    def __init__(
        self,
        reason: str,
        *,
        failure_category: str | None = None,
        engine_version: str = "",
        diagnostics: ExecutionDiagnostics | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        if failure_category is None:
            failure_category = (
                FAILURE_CATEGORY_UNKNOWN
                if reason == "engine_nonzero"
                else FAILURE_CATEGORY_NOT_APPLICABLE
            )
        self.failure_category = (
            failure_category
            if failure_category in FAILURE_CATEGORIES
            else FAILURE_CATEGORY_UNKNOWN
        )
        self.engine_version = engine_version
        self.diagnostics = diagnostics or ExecutionDiagnostics()


class EngineTimeout(TimeoutError):
    """The fixed engine invocation timed out; raw process output is discarded."""

    def __init__(
        self,
        *,
        engine_version: str,
        diagnostics: ExecutionDiagnostics,
    ) -> None:
        super().__init__("engine_timeout")
        self.engine_version = engine_version
        self.diagnostics = diagnostics


class _BoundedStderrCapture:
    """Drain stderr while retaining at most one 64 KiB in-memory buffer."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._oversize = False

    def drain(self, read_fd: int) -> None:
        with os.fdopen(read_fd, "rb", closefd=True) as stream:
            while chunk := stream.read(8192):
                remaining = MAX_FAILURE_EVENT_BYTES - len(self._buffer)
                if remaining > 0:
                    self._buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._oversize = True

    def take(self) -> tuple[bytearray, bool]:
        raw = self._buffer
        self._buffer = bytearray()
        return raw, self._oversize


@dataclass(frozen=True)
class SmokeEvidence:
    exact_sha: str
    workflow_run: str
    engine: str
    engine_version: str
    model_version: str
    result_code: str
    failure_category: str
    failure_phase: str
    engine_exit_state: str
    stdout_state: str
    stderr_state: str
    setup_ms: int
    engine_ms: int
    classification_ms: int
    duration_ms: int
    input_sha256: str
    output_length: int
    output_sha256: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _duration_ms(started: float) -> int:
    return min(
        MAX_EVIDENCE_DURATION_MS,
        max(0, int((time.monotonic() - started) * 1000)),
    )


def _bounded_ms(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(MAX_EVIDENCE_DURATION_MS, max(0, value))


def _sanitize_diagnostics(
    diagnostics: ExecutionDiagnostics | None,
) -> ExecutionDiagnostics:
    observed = diagnostics or ExecutionDiagnostics()
    return ExecutionDiagnostics(
        failure_phase=(
            observed.failure_phase
            if observed.failure_phase in FAILURE_PHASES
            else "ENGINE_LAUNCH"
        ),
        engine_exit_state=(
            observed.engine_exit_state
            if observed.engine_exit_state in ENGINE_EXIT_STATES
            else "NOT_OBSERVED"
        ),
        stdout_state=(
            observed.stdout_state
            if observed.stdout_state in STDOUT_STATES
            else "NOT_OBSERVED"
        ),
        stderr_state=(
            observed.stderr_state
            if observed.stderr_state in STDERR_STATES
            else "NOT_OBSERVED"
        ),
        setup_ms=_bounded_ms(observed.setup_ms),
        engine_ms=_bounded_ms(observed.engine_ms),
        classification_ms=_bounded_ms(observed.classification_ms),
    )


def _prefix_setup_ms(
    diagnostics: ExecutionDiagnostics,
    prefix_ms: int,
) -> ExecutionDiagnostics:
    observed = _sanitize_diagnostics(diagnostics)
    return replace(
        observed,
        setup_ms=_bounded_ms(_bounded_ms(prefix_ms) + observed.setup_ms),
    )


def _engine_exit_state(returncode: int | None) -> str:
    if returncode is None:
        return "NOT_OBSERVED"
    if returncode == 0:
        return "ZERO"
    if returncode < 0:
        return "SIGNAL"
    return "NONZERO"


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value:
        raise BlockedConfiguration(key)
    return value


def validate_configuration(env: Mapping[str, str]) -> tuple[str, str]:
    """Validate every human/configuration gate without revealing values."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise BlockedConfiguration("event")
    default_branch = _required(env, "ANYGARDEN_DEFAULT_BRANCH")
    if env.get("GITHUB_REF") != f"refs/heads/{default_branch}":
        raise BlockedConfiguration("default_branch")
    exact_sha = _required(env, "GITHUB_SHA")
    checkout_sha = _required(env, "ANYGARDEN_CHECKOUT_SHA")
    if not SHA_PATTERN.fullmatch(exact_sha) or checkout_sha != exact_sha:
        raise BlockedConfiguration("exact_sha")
    if env.get("ANYGARDEN_SMOKE_ENVIRONMENT_PROTECTED") != "true":
        raise BlockedConfiguration("protected_environment")
    if env.get("ANYGARDEN_SMOKE_APPROVED") != "true":
        raise BlockedConfiguration("human_approval")
    if env.get("ANYGARDEN_SMOKE_BUDGET_POLICY") != "vendor-daily-cap-1":
        raise BlockedConfiguration("budget")
    if env.get("ANYGARDEN_SMOKE_EGRESS_POLICY") != "vendor-only":
        raise BlockedConfiguration("egress")
    validate_proxy_url(env)
    if env.get("ANYGARDEN_SMOKE_CREDENTIAL_SCOPE") != "low-privilege-test-only":
        raise BlockedConfiguration("credential_scope")
    image = _required(env, "ANYGARDEN_SMOKE_CONTAINER_IMAGE")
    if not IMAGE_PATTERN.fullmatch(image):
        raise BlockedConfiguration("pinned_image")
    model = _required(env, "ANYGARDEN_SMOKE_MODEL")
    if not MODEL_PATTERN.fullmatch(model):
        raise BlockedConfiguration("model")
    return exact_sha, model


def validate_proxy_url(env: Mapping[str, str]) -> str:
    """Accept only a credential-free private IPv4 HTTP proxy endpoint."""
    value = _required(env, "ANYGARDEN_SMOKE_PROXY_URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        address = IPv4Address(parsed.hostname or "")
    except (AddressValueError, ValueError):
        raise BlockedConfiguration("proxy") from None
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port is None
        or port <= 0
        or not any(address in network for network in RFC1918_NETWORKS)
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or value != f"http://{address}:{port}"
    ):
        raise BlockedConfiguration("proxy")
    return value


def validate_proxy_transport(env: Mapping[str, str], proxy_url: str) -> None:
    if env.get("HTTP_PROXY") != proxy_url or env.get("HTTPS_PROXY") != proxy_url:
        raise BlockedConfiguration("proxy")


def build_command(model: str) -> list[str]:
    """Return the fixed one-invocation command; no caller prompt/flags exist."""
    return [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-c",
        "approval_policy=untrusted",
        "-c",
        "model_reasoning_effort=minimal",
        "-m",
        model,
        "-",
    ]


def prepare_runtime_state(env: Mapping[str, str]) -> dict[str, str]:
    """Create the fixed private state directories on the container tmpfs."""
    state_dirs = {
        "HOME": RUNTIME_STATE_ROOT / "home",
        "CODEX_HOME": RUNTIME_STATE_ROOT / "codex",
    }
    for key, path in state_dirs.items():
        if env.get(key) != str(path):
            raise BlockedConfiguration("runtime_state")
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        except OSError:
            raise BlockedConfiguration("runtime_state") from None
    return {key: str(path) for key, path in state_dirs.items()}


def build_child_env(
    env: Mapping[str, str],
    credential: str,
    runtime_state: Mapping[str, str],
    proxy_url: str,
) -> dict[str, str]:
    return {
        "PATH": env.get("PATH", ""),
        "HOME": runtime_state["HOME"],
        "CODEX_HOME": runtime_state["CODEX_HOME"],
        "OPENAI_API_KEY": credential,
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "LANG": "C.UTF-8",
    }


def parse_response(raw: bytes) -> bytes:
    """Extract the canary while rejecting tool/approval activity."""
    if len(raw) > 64 * 1024:
        raise SmokeFailure("protocol_output_limit")
    texts: list[str] = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise SmokeFailure("protocol_shape")
        event_type = str(event.get("type", ""))
        if "approval" in event_type:
            raise SmokeFailure("approval_requested")
        if event_type in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type not in ALLOWED_ITEM_TYPES:
                raise SmokeFailure("tool_requested")
            if (
                event_type == "item.completed"
                and item_type == "agent_message"
                and isinstance(item, dict)
            ):
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
    response = "\n".join(texts).strip().encode()
    if len(response) > MAX_RESPONSE_BYTES:
        raise SmokeFailure("response_limit")
    if response != CANARY_RESPONSE.encode():
        raise SmokeFailure("canary_mismatch")
    return response


def _http_status_category(status: int) -> str | None:
    if status in {401, 403}:
        return "AUTH_REJECTED"
    if status == 429:
        return "RATE_LIMIT"
    if 500 <= status <= 599:
        return "UPSTREAM"
    return None


def _failure_message_category_occurrences(message: str) -> tuple[str, ...]:
    """Project one bounded message while preserving real repetition.

    Codex flattens one HTTP failure into a status prefix plus the provider's
    fixed error copy.  One status and one semantic signal that agree are one
    corroborated failure, not two independent events.  Repeated statuses or
    repeated semantic signals remain ambiguous and fail closed.
    """
    if len(message.encode("utf-8")) > MAX_FAILURE_MESSAGE_BYTES:
        return ()
    normalized = message.casefold()
    statuses = [
        int(match.group("status")) for match in HTTP_STATUS_PATTERN.finditer(message)
    ]
    if len(statuses) > 1:
        # Preserve every HTTP status occurrence before assigning meaning.  In
        # particular, 400/404 are meaningful only with one model-access copy;
        # dropping them here would let mixed or repeated statuses look unique.
        return tuple(
            _http_status_category(status) or FAILURE_CATEGORY_UNKNOWN
            for status in statuses
        )
    status_categories = [
        category
        for status in statuses
        if (category := _http_status_category(status)) is not None
    ]

    signal_categories: list[str] = []

    def add_signal_occurrences(signals: Sequence[str], category: str) -> int:
        spans: list[tuple[int, int]] = []
        for signal_text in signals:
            start = 0
            while (index := normalized.find(signal_text, start)) >= 0:
                spans.append((index, index + len(signal_text)))
                start = index + 1
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if merged and start < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        signal_categories.extend([category] * len(merged))
        return len(merged)

    add_signal_occurrences(AUTH_FAILURE_SIGNALS, "AUTH_REJECTED")
    if not statuses or set(statuses) <= {400, 403, 404}:
        add_signal_occurrences(MODEL_FAILURE_SIGNALS, "MODEL_ACCESS")
    if not statuses or set(statuses) <= {400}:
        config_matches = add_signal_occurrences(
            ENGINE_CONFIG_FAILURE_SIGNALS, "ENGINE_CONFIG"
        )
        if config_matches == 0 and (
            "model_reasoning_effort" in normalized
            and any(
                word in normalized
                for word in ("invalid", "unknown", "unrecognized", "unsupported")
            )
        ):
            signal_categories.append("ENGINE_CONFIG")
    if not statuses and any(
        signal in normalized for signal in RATE_LIMIT_FAILURE_SIGNALS
    ):
        add_signal_occurrences(RATE_LIMIT_FAILURE_SIGNALS, "RATE_LIMIT")
    if not statuses and any(
        signal in normalized for signal in UPSTREAM_FAILURE_SIGNALS
    ):
        add_signal_occurrences(UPSTREAM_FAILURE_SIGNALS, "UPSTREAM")
    if len(status_categories) > 1 or len(signal_categories) > 1:
        return (*status_categories, *signal_categories)
    status_category = status_categories[0] if status_categories else None
    signal_category = signal_categories[0] if signal_categories else None
    if status_category is None:
        return (signal_category,) if signal_category is not None else ()
    if signal_category is None:
        return (status_category,)
    if status_category == signal_category:
        return (status_category,)
    # A 403 with explicit model-access copy is model authorization, whereas a
    # generic 403 remains an authentication/credential rejection.
    if statuses == [403] and signal_category == "MODEL_ACCESS":
        return ("MODEL_ACCESS",)
    # Concrete HTTP status categories keep precedence over unrelated copy.
    return (status_category,)


def _classify_failure_message(message: str) -> str:
    # Codex 0.146 exposes text but not its internal error enum. Adopt a result
    # only when exactly one fixed category matches; ambiguity fails closed.
    categories = _failure_message_category_occurrences(message)
    if len(categories) != 1:
        return FAILURE_CATEGORY_UNKNOWN
    return next(iter(categories))


def classify_failure_observation(raw: bytes) -> tuple[str, str]:
    """Project JSONL to closed category/stdout state without retaining text."""
    if not raw:
        return FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT, "EMPTY"
    if len(raw) > MAX_FAILURE_EVENT_BYTES:
        return FAILURE_CATEGORY_UNKNOWN, "OVERSIZE"
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
    saw_nonempty_line = False
    error_categories: list[str] = []
    terminal_categories: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        saw_nonempty_line = True
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
        if not isinstance(event, dict):
            return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
        event_type = event.get("type")
        message: object | None = None
        if event_type == "error":
            message = event.get("message")
        elif event_type == "turn.failed":
            error = event.get("error")
            if not isinstance(error, dict):
                return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
            message = error.get("message")
        else:
            continue
        if not isinstance(message, str):
            return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
        category = _classify_failure_message(message)
        if event_type == "turn.failed":
            terminal_categories.append(category)
        else:
            error_categories.append(category)
    failure_count = len(error_categories) + len(terminal_categories)
    if not saw_nonempty_line or failure_count == 0:
        return FAILURE_CATEGORY_UNKNOWN, "NON_FAILURE_OUTPUT"
    if len(terminal_categories) == 1:
        terminal_category = terminal_categories[0]
        recognized_errors = {
            category
            for category in error_categories
            if category != FAILURE_CATEGORY_UNKNOWN
        }
        if terminal_category != FAILURE_CATEGORY_UNKNOWN and recognized_errors <= {
            terminal_category
        }:
            state = "SINGLE_FAILURE_EVENT" if failure_count == 1 else "TERMINAL_FAILURE"
            return terminal_category, state
        state = (
            "SINGLE_FAILURE_EVENT" if failure_count == 1 else "MULTIPLE_FAILURE_EVENTS"
        )
        return FAILURE_CATEGORY_UNKNOWN, state
    if len(terminal_categories) > 1 or len(error_categories) > 1:
        return FAILURE_CATEGORY_UNKNOWN, "MULTIPLE_FAILURE_EVENTS"
    return error_categories[0], "SINGLE_FAILURE_EVENT"


def classify_failure(raw: bytes) -> str:
    """Compatibility helper returning only the closed failure category."""
    return classify_failure_observation(raw)[0]


def classify_stderr_observation(
    raw: bytes | bytearray, *, oversize: bool = False
) -> tuple[str, str]:
    """Classify bounded stderr in memory without retaining any source text."""
    if oversize:
        return FAILURE_CATEGORY_UNKNOWN, "OVERSIZE"
    if not raw:
        return FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT, "EMPTY"
    if len(raw) > MAX_FAILURE_EVENT_BYTES:
        return FAILURE_CATEGORY_UNKNOWN, "OVERSIZE"
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return FAILURE_CATEGORY_UNKNOWN, "MALFORMED"
    matched_categories: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        categories = _failure_message_category_occurrences(line)
        if len(categories) > 1:
            return FAILURE_CATEGORY_UNKNOWN, "MULTIPLE_FAILURE_SIGNALS"
        if categories:
            matched_categories.append(next(iter(categories)))
    if not matched_categories:
        return FAILURE_CATEGORY_UNKNOWN, "UNRECOGNIZED"
    if len(matched_categories) > 1:
        return FAILURE_CATEGORY_UNKNOWN, "MULTIPLE_FAILURE_SIGNALS"
    return matched_categories[0], "SINGLE_FAILURE_SIGNAL"


def _terminate_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=TERM_GRACE_SECONDS)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def execute(model: str, env: Mapping[str, str]) -> ExecutionResult:
    setup_started = time.monotonic()
    if (
        env.get("ANYGARDEN_SMOKE_RUNTIME_ISOLATED")
        != "container-readonly-empty-workspace"
    ):
        raise BlockedConfiguration("runtime_isolation")
    if any(Path.cwd().iterdir()):
        raise BlockedConfiguration("workspace_not_empty")
    proxy_url = validate_proxy_url(env)
    validate_proxy_transport(env, proxy_url)
    credential = _required(env, "OPENAI_API_KEY")
    runtime_state = prepare_runtime_state(env)
    codex = shutil.which("codex")
    if not codex:
        raise BlockedConfiguration("engine_missing")
    try:
        version = subprocess.run(
            [codex, "--version"], capture_output=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        raise BlockedConfiguration("engine_version") from None
    if version.returncode != 0:
        raise BlockedConfiguration("engine_version")
    engine_version = version.stdout.decode(errors="replace").strip()
    if not ENGINE_VERSION_PATTERN.fullmatch(engine_version):
        raise BlockedConfiguration("engine_version")
    child_env = build_child_env(env, credential, runtime_state, proxy_url)
    stderr_read_fd, stderr_write_fd = os.pipe()
    try:
        proc = subprocess.Popen(
            build_command(model),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_write_fd,
            env=child_env,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        os.close(stderr_read_fd)
        os.close(stderr_write_fd)
        raise SmokeFailure(
            "engine_launch",
            engine_version=engine_version,
            diagnostics=ExecutionDiagnostics(
                failure_phase="ENGINE_LAUNCH",
                setup_ms=_duration_ms(setup_started),
            ),
        ) from None
    os.close(stderr_write_fd)
    stderr_capture = _BoundedStderrCapture()
    stderr_thread = threading.Thread(
        target=stderr_capture.drain,
        args=(stderr_read_fd,),
        daemon=True,
    )
    stderr_thread.start()
    setup_ms = _duration_ms(setup_started)
    engine_started = time.monotonic()
    try:
        stdout, _discarded_stderr = proc.communicate(
            input=CANARY_PROMPT.encode(), timeout=HARD_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        _terminate_group(proc)
        stderr_thread.join()
        stderr, _stderr_oversize = stderr_capture.take()
        del stderr
        raise EngineTimeout(
            engine_version=engine_version,
            diagnostics=ExecutionDiagnostics(
                failure_phase="ENGINE_EXECUTION",
                engine_exit_state="SIGNAL",
                stdout_state="NOT_OBSERVED",
                setup_ms=setup_ms,
                engine_ms=_duration_ms(engine_started),
            ),
        ) from None
    except (OSError, subprocess.SubprocessError):
        _terminate_group(proc)
        stderr_thread.join()
        stderr, _stderr_oversize = stderr_capture.take()
        del stderr
        raise SmokeFailure(
            "engine_execution",
            engine_version=engine_version,
            diagnostics=ExecutionDiagnostics(
                failure_phase="ENGINE_EXECUTION",
                engine_exit_state=_engine_exit_state(proc.returncode),
                stdout_state="NOT_OBSERVED",
                stderr_state="NOT_OBSERVED",
                setup_ms=setup_ms,
                engine_ms=_duration_ms(engine_started),
            ),
        ) from None
    stderr_thread.join()
    stderr, stderr_oversize = stderr_capture.take()
    engine_ms = _duration_ms(engine_started)
    if proc.returncode != 0:
        classification_started = time.monotonic()
        failure_category, stdout_state = classify_failure_observation(stdout)
        stderr_category, stderr_state = classify_stderr_observation(
            stderr, oversize=stderr_oversize
        )
        del stderr
        if stdout_state in {"EMPTY", "NON_FAILURE_OUTPUT"}:
            if stderr_state == "SINGLE_FAILURE_SIGNAL":
                failure_category = stderr_category
                failure_phase = "STDERR_CLASSIFICATION"
            elif stdout_state == "EMPTY" and stderr_state == "EMPTY":
                failure_category = FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT
                failure_phase = "ENGINE_EXIT"
            else:
                failure_category = FAILURE_CATEGORY_UNKNOWN
                failure_phase = "STDERR_CLASSIFICATION"
        else:
            failure_phase = "STDOUT_CLASSIFICATION"
        classification_ms = _duration_ms(classification_started)
        diagnostics = ExecutionDiagnostics(
            failure_phase=failure_phase,
            engine_exit_state=_engine_exit_state(proc.returncode),
            stdout_state=stdout_state,
            stderr_state=stderr_state,
            setup_ms=setup_ms,
            engine_ms=engine_ms,
            classification_ms=classification_ms,
        )
        del stdout
        raise SmokeFailure(
            "engine_nonzero",
            failure_category=failure_category,
            engine_version=engine_version,
            diagnostics=diagnostics,
        )
    del stderr
    classification_started = time.monotonic()
    _category, stdout_state = classify_failure_observation(stdout)
    try:
        output = parse_response(stdout)
    except SmokeFailure as exc:
        diagnostics = ExecutionDiagnostics(
            failure_phase="RESPONSE_PARSE",
            engine_exit_state="ZERO",
            stdout_state=stdout_state,
            setup_ms=setup_ms,
            engine_ms=engine_ms,
            classification_ms=_duration_ms(classification_started),
        )
        del stdout
        raise SmokeFailure(
            exc.reason,
            failure_category=exc.failure_category,
            engine_version=engine_version,
            diagnostics=diagnostics,
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        diagnostics = ExecutionDiagnostics(
            failure_phase="RESPONSE_PARSE",
            engine_exit_state="ZERO",
            stdout_state=stdout_state,
            setup_ms=setup_ms,
            engine_ms=engine_ms,
            classification_ms=_duration_ms(classification_started),
        )
        del stdout
        raise SmokeFailure(
            "protocol_shape",
            engine_version=engine_version,
            diagnostics=diagnostics,
        ) from None
    diagnostics = ExecutionDiagnostics(
        failure_phase="RESPONSE_PARSE",
        engine_exit_state="ZERO",
        stdout_state=stdout_state,
        setup_ms=setup_ms,
        engine_ms=engine_ms,
        classification_ms=_duration_ms(classification_started),
    )
    del stdout
    return ExecutionResult(
        output=output,
        engine_version=engine_version,
        diagnostics=diagnostics,
    )


def make_evidence(
    env: Mapping[str, str],
    result: str,
    started: float,
    *,
    output: bytes = b"",
    engine_version: str = "",
    failure_category: str = FAILURE_CATEGORY_NOT_APPLICABLE,
    diagnostics: ExecutionDiagnostics | None = None,
) -> SmokeEvidence:
    projected_category = (
        failure_category
        if failure_category in FAILURE_CATEGORIES
        else FAILURE_CATEGORY_UNKNOWN
    )
    projected_diagnostics = _sanitize_diagnostics(diagnostics)
    if projected_category == FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT and not (
        result == "FAIL_ENGINE_NONZERO"
        and projected_diagnostics.engine_exit_state == "NONZERO"
        and projected_diagnostics.stdout_state == "EMPTY"
        and projected_diagnostics.stderr_state == "EMPTY"
    ):
        projected_category = FAILURE_CATEGORY_UNKNOWN
    projected_engine_version = (
        engine_version if ENGINE_VERSION_PATTERN.fullmatch(engine_version) else ""
    )
    return SmokeEvidence(
        exact_sha=env.get("GITHUB_SHA", ""),
        workflow_run=env.get("GITHUB_RUN_ID", ""),
        engine="codex-cli",
        engine_version=projected_engine_version,
        model_version="",
        result_code=result,
        failure_category=projected_category,
        failure_phase=projected_diagnostics.failure_phase,
        engine_exit_state=projected_diagnostics.engine_exit_state,
        stdout_state=projected_diagnostics.stdout_state,
        stderr_state=projected_diagnostics.stderr_state,
        setup_ms=projected_diagnostics.setup_ms,
        engine_ms=projected_diagnostics.engine_ms,
        classification_ms=projected_diagnostics.classification_ms,
        duration_ms=_duration_ms(started),
        input_sha256=_sha256(CANARY_PROMPT.encode()),
        output_length=len(output),
        output_sha256=_sha256(output) if output else "",
    )


def write_evidence(path: Path, evidence: SmokeEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(evidence), sort_keys=True) + "\n", encoding="utf-8"
    )


def run(mode: str, evidence_path: Path, env: Mapping[str, str]) -> int:
    started = time.monotonic()
    configuration_ms = 0
    try:
        _exact_sha, model = validate_configuration(env)
        configuration_ms = _duration_ms(started)
        if mode == "preflight":
            write_evidence(
                evidence_path,
                make_evidence(
                    env,
                    "PREFLIGHT_PASS",
                    started,
                    diagnostics=ExecutionDiagnostics(setup_ms=configuration_ms),
                ),
            )
            return 0
        execution = execute(model, env)
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                "PASS",
                started,
                output=execution.output,
                engine_version=execution.engine_version,
                diagnostics=_prefix_setup_ms(execution.diagnostics, configuration_ms),
            ),
        )
        return 0
    except BlockedConfiguration:
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                "BLOCKED_CONFIGURATION",
                started,
                diagnostics=ExecutionDiagnostics(setup_ms=_duration_ms(started)),
            ),
        )
        return 2
    except EngineTimeout as exc:
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                "TIMEOUT",
                started,
                engine_version=exc.engine_version,
                diagnostics=_prefix_setup_ms(exc.diagnostics, configuration_ms),
            ),
        )
        return 124
    except TimeoutError:
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                "TIMEOUT",
                started,
                diagnostics=ExecutionDiagnostics(
                    failure_phase="ENGINE_EXECUTION",
                    setup_ms=_duration_ms(started),
                ),
            ),
        )
        return 124
    except SmokeFailure as exc:
        reason = exc.reason
        failure_category = (
            exc.failure_category
            if reason == "engine_nonzero"
            else FAILURE_CATEGORY_NOT_APPLICABLE
        )
        result = SMOKE_FAILURE_RESULT_CODES.get(reason, "FAIL")
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                result,
                started,
                engine_version=exc.engine_version,
                failure_category=failure_category,
                diagnostics=_prefix_setup_ms(exc.diagnostics, configuration_ms),
            ),
        )
        return 1
    except Exception:  # noqa: BLE001 - evidence must redact every unknown failure
        write_evidence(
            evidence_path,
            make_evidence(
                env,
                "FAIL",
                started,
                diagnostics=ExecutionDiagnostics(setup_ms=_duration_ms(started)),
            ),
        )
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "run"))
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.mode, args.evidence, os.environ)


if __name__ == "__main__":
    raise SystemExit(main())
