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
import signal
import socket
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import BinaryIO

import engine_smoke_gate as smoke

STUB_MODEL = "gpt-5.6-sol"
STUB_CREDENTIAL = "stub-only-not-a-credential"
STUB_TIMEOUT_SECONDS = smoke.HARD_TIMEOUT_SECONDS
CASE_STATE_DIRS = {
    "AUTH_401": "auth-401",
    "MODEL_403": "model-403",
    "MODEL_404": "model-404",
    "EMPTY_200": "empty-200",
}


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
            audit.path_valid = audit.path_valid and self.path == "/v1/responses"
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
    observation = StubObservation(
        case=case.name,
        expected_category=case.expected_category,
        matched=(
            exit_state == "NONZERO"
            and audit.count > 0
            and audit.path_valid
            and observed_category == case.expected_category
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
                f"stdout={observation.stdout_state} stderr={observation.stderr_state}"
            )
    return 0 if payload["all_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
