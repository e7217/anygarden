"""Provider-free Codex wire-response validation for the engine smoke gate.

Run this only inside the immutable engine-smoke image with Docker networking
set to ``none``.  The script serves fixed Responses-API fixtures on loopback,
executes the production smoke command with only a fixed ``openai_base_url``
override, and emits closed observations.  Raw stdout, stderr, credentials,
request bodies, endpoints, and model values are never written or printed.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import signal
import socket
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from ipaddress import AddressValueError, IPv4Address
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import urlsplit

import engine_smoke_gate as smoke

STUB_MODEL = "gpt-5.6-sol"
STUB_CREDENTIAL = "stub-only-not-a-credential"
STUB_TIMEOUT_SECONDS = smoke.HARD_TIMEOUT_SECONDS
STUB_RESPONSES_PATH = "/v1/responses"
CASE_STATE_DIRS = {
    "AUTH_401": "auth-401",
    "MODEL_403": "model-403",
    "MODEL_404": "model-404",
    "EMPTY_200": "empty-200",
    "MIXED_401_404": "mixed-401-404",
    "REPEATED_404": "repeated-404",
}
CANONICAL_CASE_NAMES = frozenset({"AUTH_401", "MODEL_403", "MODEL_404", "EMPTY_200"})

# Minimal fixed fragments derived from direct observation of Codex 0.146 in the
# isolated loopback harness.  Dynamic URLs, request IDs, timestamps, and raw
# provider copy are deliberately excluded.  These fragments are validation
# inputs only; production classification remains centralized in
# ``engine_smoke_gate``.
DERIVED_ALLOWLIST_PATTERNS = {
    "AUTH_401": ("unexpected status 401", "incorrect api key"),
    "MODEL_403": (
        "unexpected status 403",
        "does not exist or you do not have access to it",
    ),
    "MODEL_404": ("unexpected status 404", "model does not exist"),
    "EMPTY_200": ("stream disconnected before completion:",),
}
DERIVED_PATTERN_IDS = {
    "AUTH_401": "HTTP_401+INCORRECT_API_KEY",
    "MODEL_403": "HTTP_403+MODEL_ACCESS_COPY",
    "MODEL_404": "HTTP_404+MODEL_NOT_FOUND_COPY",
    "EMPTY_200": "STREAM_DISCONNECTED_BEFORE_COMPLETION",
}
PATH_ALIAS_WARNING_PATTERN = re.compile(
    r"^WARNING: proceeding, even though we could not create PATH aliases: "
    r'Refusing to create helper binaries under temporary dir "'
    r'(?P<temp_dir>[^"\r\n]+)" \(codex_home: AbsolutePathBuf\("'
    r'(?P<codex_home>[^"\r\n]+)"\)\)$'
)
WEBSOCKET_FALLBACK_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,9})?Z) ERROR codex_api::endpoint::responses_websocket: "
    r"failed to connect to websocket: HTTP error: 501 Not Implemented, "
    r"url: (?P<url>\S+)$"
)


@dataclass(frozen=True)
class StubCase:
    name: str
    status: int
    content_type: str
    body: bytes
    expected_category: str


@dataclass(frozen=True)
class StubObservation:
    case: str
    expected_category: str
    matched: bool
    exit_state: str
    request_count: int
    request_path_valid: bool
    stdout_bytes: int
    stderr_bytes: int
    stdout_category: str
    stdout_state: str
    stdout_failure_events: int
    stdout_event_categories: tuple[str, ...]
    stderr_category: str
    stderr_state: str
    category_source: str
    derived_pattern_id: str
    derived_pattern_matched: bool
    stderr_structure: str
    historical_canary_shape: str
    stdout_oversize: bool
    stderr_oversize: bool


CASES = (
    StubCase(
        name="AUTH_401",
        status=401,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
            separators=(",", ":"),
        ).encode(),
        expected_category="AUTH_REJECTED",
    ),
    StubCase(
        name="MODEL_403",
        status=403,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {
                    "message": (
                        "The requested model does not exist or you do not have "
                        "access to it"
                    ),
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_accessible",
                }
            },
            separators=(",", ":"),
        ).encode(),
        expected_category="MODEL_ACCESS",
    ),
    StubCase(
        name="MODEL_404",
        status=404,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {
                    "message": "The requested model does not exist",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            },
            separators=(",", ":"),
        ).encode(),
        expected_category="MODEL_ACCESS",
    ),
    StubCase(
        name="EMPTY_200",
        status=200,
        content_type="text/event-stream",
        body=b"",
        expected_category="UPSTREAM",
    ),
    StubCase(
        name="MIXED_401_404",
        status=401,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {
                    "message": "unexpected status 404 Not Found",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
            separators=(",", ":"),
        ).encode(),
        expected_category=smoke.FAILURE_CATEGORY_UNKNOWN,
    ),
    StubCase(
        name="REPEATED_404",
        status=404,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {
                    "message": "unexpected status 404 Not Found: Model not found",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            },
            separators=(",", ":"),
        ).encode(),
        expected_category=smoke.FAILURE_CATEGORY_UNKNOWN,
    ),
)


class _BoundedCapture:
    """Drain one pipe while retaining at most the production 64 KiB cap."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._oversize = False

    def drain(self, stream: BinaryIO) -> None:
        try:
            while chunk := stream.read(8192):
                remaining = smoke.MAX_FAILURE_EVENT_BYTES - len(self._buffer)
                if remaining > 0:
                    self._buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self._oversize = True
        finally:
            stream.close()

    def take(self) -> tuple[bytes, bool]:
        raw = bytes(self._buffer)
        self._buffer = bytearray()
        return raw, self._oversize


@dataclass
class _RequestAudit:
    count: int = 0
    path_valid: bool = True


def _assert_loopback_only() -> None:
    interfaces = {name for _index, name in socket.if_nameindex()}
    if interfaces != {"lo"}:
        raise RuntimeError("network_not_isolated")


def _build_stub_command(base_url: str) -> list[str]:
    command = smoke.build_command(STUB_MODEL)
    model_index = command.index("-m")
    return [
        *command[:model_index],
        "-c",
        f"openai_base_url={json.dumps(base_url)}",
        *command[model_index:],
    ]


def _child_env(case_name: str) -> dict[str, str]:
    state_dir = f"/tmp/anygarden-smoke-stub/{CASE_STATE_DIRS[case_name]}"
    home = f"{state_dir}/home"
    codex_home = f"{state_dir}/codex"
    os.makedirs(home, mode=0o700, exist_ok=True)
    os.makedirs(codex_home, mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    os.chmod(codex_home, 0o700)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": home,
        "CODEX_HOME": codex_home,
        "OPENAI_API_KEY": STUB_CREDENTIAL,
        "LANG": "C.UTF-8",
    }


def _make_handler(
    case: StubCase, audit: _RequestAudit
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            audit.count += 1
            audit.path_valid = audit.path_valid and self.path == STUB_RESPONSES_PATH
            remaining = int(self.headers.get("Content-Length", "0"))
            while remaining > 0:
                chunk = self.rfile.read(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self.send_response(case.status)
            self.send_header("Content-Type", case.content_type)
            self.send_header("Content-Length", str(len(case.body)))
            self.send_header("x-request-id", "req_stub")
            self.send_header("Connection", "close")
            self.end_headers()
            if case.body:
                self.wfile.write(case.body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _execute(
    command: Sequence[str], case_name: str
) -> tuple[str, bytes, bool, bytes, bool]:
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(case_name),
        start_new_session=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    stdout_capture = _BoundedCapture()
    stderr_capture = _BoundedCapture()
    stdout_thread = threading.Thread(
        target=stdout_capture.drain, args=(proc.stdout,), daemon=True
    )
    stderr_thread = threading.Thread(
        target=stderr_capture.drain, args=(proc.stderr,), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        proc.stdin.write(smoke.CANARY_PROMPT.encode())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    try:
        proc.wait(timeout=STUB_TIMEOUT_SECONDS)
        exit_state = "ZERO" if proc.returncode == 0 else "NONZERO"
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        exit_state = "TIMEOUT"
    stdout_thread.join()
    stderr_thread.join()
    stdout, stdout_oversize = stdout_capture.take()
    stderr, stderr_oversize = stderr_capture.take()
    return exit_state, stdout, stdout_oversize, stderr, stderr_oversize


def _project_stdout_failure_events(raw: bytes | bytearray) -> tuple[str, ...]:
    """Return one closed projection per Codex terminal/error event."""
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return (smoke.FAILURE_CATEGORY_UNKNOWN,)
    categories: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return (smoke.FAILURE_CATEGORY_UNKNOWN,)
        if not isinstance(event, dict):
            return (smoke.FAILURE_CATEGORY_UNKNOWN,)
        message: object | None = None
        if event.get("type") == "error":
            message = event.get("message")
        elif event.get("type") == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                message = error.get("message")
        if isinstance(message, str):
            occurrences = smoke._failure_message_category_occurrences(message)
            category = (
                occurrences[0]
                if len(occurrences) == 1
                else smoke.FAILURE_CATEGORY_UNKNOWN
            )
            categories.append(f"{event.get('type')}:{category}")
    return tuple(categories)


def _terminal_failure_messages(raw: bytes | bytearray) -> tuple[str, ...]:
    """Return terminal messages for in-memory validation only.

    Callers must discard the returned strings with the raw buffers.  They are
    never included in ``StubObservation`` or serialized output.
    """
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return ()
    messages: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return ()
        if not isinstance(event, dict) or event.get("type") != "turn.failed":
            continue
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        if not isinstance(message, str):
            return ()
        messages.append(message)
    return tuple(messages)


def _matches_derived_pattern(case_name: str, raw: bytes | bytearray) -> bool:
    expected = DERIVED_ALLOWLIST_PATTERNS.get(case_name)
    if expected is None:
        return False
    messages = _terminal_failure_messages(raw)
    if len(messages) != 1:
        return False
    normalized = messages[0].casefold()
    matched = all(fragment in normalized for fragment in expected)
    del messages
    return matched


def _project_stderr_structure(raw: bytes | bytearray) -> str:
    """Project observed Codex stderr to a closed, non-authoritative shape."""
    if not raw:
        return "EMPTY"
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        return "OTHER"
    saw_fallback = False
    for line in lines:
        if not line.strip() or _is_expected_path_alias_warning(line):
            continue
        if _is_expected_websocket_fallback(line):
            saw_fallback = True
            continue
        return "OTHER"
    return "WEBSOCKET_FALLBACK_ONLY" if saw_fallback else "OTHER"


def _is_expected_path_alias_warning(line: str) -> bool:
    match = PATH_ALIAS_WARNING_PATTERN.fullmatch(line)
    if match is None:
        return False
    temp_dir = PurePosixPath(match.group("temp_dir"))
    codex_home = PurePosixPath(match.group("codex_home"))
    return (
        temp_dir == PurePosixPath(smoke.RUNTIME_STATE_ROOT.as_posix())
        and codex_home.is_absolute()
        and codex_home.parts[: len(temp_dir.parts)] == temp_dir.parts
        and ".." not in codex_home.parts
        and codex_home.name == "codex"
    )


def _is_expected_websocket_fallback(line: str) -> bool:
    match = WEBSOCKET_FALLBACK_PATTERN.fullmatch(line)
    if match is None:
        return False
    try:
        parsed = urlsplit(match.group("url"))
        address = IPv4Address(parsed.hostname or "")
        port = parsed.port
    except (AddressValueError, ValueError):
        return False
    return (
        parsed.scheme == "ws"
        and address.is_loopback
        and port is not None
        and 0 < port <= 65535
        and parsed.username is None
        and parsed.password is None
        and parsed.path == STUB_RESPONSES_PATH
        and not parsed.query
        and not parsed.fragment
    )


def run_case(case: StubCase) -> StubObservation:
    audit = _RequestAudit()
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _make_handler(case, audit)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        command = _build_stub_command(f"http://127.0.0.1:{port}/v1")
        exit_state, stdout, stdout_oversize, stderr, stderr_oversize = _execute(
            command, case.name
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    stdout_category, stdout_state = smoke.classify_failure_observation(stdout)
    stdout_event_categories = _project_stdout_failure_events(stdout)
    stderr_category, stderr_state = smoke.classify_stderr_observation(
        stderr, oversize=stderr_oversize
    )
    derived_pattern_matched = _matches_derived_pattern(case.name, stdout)
    stderr_structure = _project_stderr_structure(stderr)
    if stdout_state in {"EMPTY", "NON_FAILURE_OUTPUT"}:
        observed_category = (
            stderr_category
            if stderr_state == "SINGLE_FAILURE_SIGNAL"
            else smoke.FAILURE_CATEGORY_UNKNOWN
        )
        if stdout_state == "EMPTY" and stderr_state == "EMPTY":
            observed_category = smoke.FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT
    else:
        observed_category = stdout_category
    if stdout_state in {"SINGLE_FAILURE_EVENT", "TERMINAL_FAILURE"}:
        category_source = "STDOUT_TERMINAL"
    elif stderr_state == "SINGLE_FAILURE_SIGNAL":
        category_source = "STDERR_ALLOWLIST"
    elif stdout_state == "EMPTY" and stderr_state == "EMPTY":
        category_source = "EMPTY_BOTH"
    else:
        category_source = "UNKNOWN"
    historical_canary_shape = "NOT_APPLICABLE"
    if case.name == "EMPTY_200":
        historical_canary_shape = (
            "MATCH"
            if exit_state == "NONZERO" and not stdout and bool(stderr)
            else "MISMATCH"
        )
    observation = StubObservation(
        case=case.name,
        expected_category=case.expected_category,
        matched=(
            exit_state == "NONZERO"
            and audit.count > 0
            and audit.path_valid
            and observed_category == case.expected_category
            and (
                case.name not in CANONICAL_CASE_NAMES
                or (
                    derived_pattern_matched
                    and category_source == "STDOUT_TERMINAL"
                    and stderr_state == "UNRECOGNIZED"
                    and stderr_structure == "WEBSOCKET_FALLBACK_ONLY"
                )
            )
            and (case.name != "EMPTY_200" or historical_canary_shape == "MISMATCH")
        ),
        exit_state=exit_state,
        request_count=audit.count,
        request_path_valid=audit.path_valid,
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        stdout_category=stdout_category,
        stdout_state=stdout_state,
        stdout_failure_events=len(stdout_event_categories),
        stdout_event_categories=stdout_event_categories,
        stderr_category=stderr_category,
        stderr_state=stderr_state,
        category_source=category_source,
        derived_pattern_id=DERIVED_PATTERN_IDS.get(case.name, "NOT_APPLICABLE"),
        derived_pattern_matched=derived_pattern_matched,
        stderr_structure=stderr_structure,
        historical_canary_shape=historical_canary_shape,
        stdout_oversize=stdout_oversize,
        stderr_oversize=stderr_oversize,
    )
    del stdout
    del stderr
    return observation


def _build_payload(observations: Sequence[StubObservation]) -> dict[str, object]:
    return {
        "network": "LOOPBACK_ONLY",
        "credential": "STUB_ONLY",
        "provider_calls": 0,
        "all_matched": all(observation.matched for observation in observations),
        "cases": [asdict(observation) for observation in observations],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _assert_loopback_only()
    observations = [run_case(case) for case in CASES]
    payload = _build_payload(observations)
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for observation in observations:
            print(
                f"{observation.case}: matched={str(observation.matched).lower()} "
                f"category={observation.stdout_category} "
                f"source={observation.category_source} "
                f"pattern={observation.derived_pattern_id} "
                f"pattern_matched={str(observation.derived_pattern_matched).lower()} "
                f"stdout={observation.stdout_state} stderr={observation.stderr_state} "
                f"stderr_structure={observation.stderr_structure} "
                f"historical={observation.historical_canary_shape}"
            )
    return 0 if payload["all_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
