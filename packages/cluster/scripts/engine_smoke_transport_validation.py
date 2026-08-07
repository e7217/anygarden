"""Provider-free transport-shape search for the Codex engine smoke.

This diagnostic runs only inside the immutable smoke image with Docker
networking set to ``none``.  It exercises fixed loopback response, proxy, and
startup controls while retaining stdout/stderr only in bounded memory.  The
serialized result contains closed classifications, byte counts, timing, and
request counters; raw output, credentials, endpoints, request bodies, and
model values are never printed or written.

The historical ``output_length=0`` evidence field was the final parsed output
length, not a raw stdout measurement.  Consequently the three-part historical
shape below is an operational search hypothesis, not a claim about the old
run: raw stdout is empty, the child exits nonzero, and elapsed time is close to
18 seconds.
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
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

import engine_smoke_gate as smoke

STUB_MODEL = "gpt-5.6-sol"
STUB_CREDENTIAL = "transport-stub-only-not-a-credential"
RESPONSES_PATH = "/v1/responses"
HISTORICAL_DURATION_MIN_MS = 15_000
HISTORICAL_DURATION_MAX_MS = 22_000
DELAY_SECONDS = 5.0
NO_RESPONSE_SECONDS = smoke.HARD_TIMEOUT_SECONDS + 5.0


class TransportMode(str, Enum):
    DELAYED_CLOSE = "delayed_close"
    DELAYED_EMPTY_SSE = "delayed_empty_sse"
    MALFORMED_SSE = "malformed_sse"
    PARTIAL_SSE = "partial_sse"
    TRUNCATED_CHUNKED = "truncated_chunked"
    WEBSOCKET_REJECT = "websocket_reject"
    PROXY_REJECT = "proxy_reject"
    NO_RESPONSE = "no_response"
    STARTUP_INVALID_CONFIG = "startup_invalid_config"


@dataclass(frozen=True)
class TransportCase:
    name: str
    mode: TransportMode


@dataclass
class RequestAudit:
    websocket_requests: int = 0
    post_requests: int = 0
    proxy_connects: int = 0
    request_path_valid: bool = True


@dataclass(frozen=True)
class TransportObservation:
    case: str
    mode: str
    exit_state: str
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int
    stdout_state: str
    stderr_state: str
    failure_category: str
    category_source: str
    websocket_requests: int
    post_requests: int
    proxy_connects: int
    request_path_valid: bool
    raw_stdout_empty: bool
    nonzero_exit: bool
    duration_near_historical: bool
    historical_shape: str
    stdout_oversize: bool
    stderr_oversize: bool


CASES = (
    TransportCase("DELAYED_CLOSE", TransportMode.DELAYED_CLOSE),
    TransportCase("DELAYED_EMPTY_SSE", TransportMode.DELAYED_EMPTY_SSE),
    TransportCase("MALFORMED_SSE", TransportMode.MALFORMED_SSE),
    TransportCase("PARTIAL_SSE", TransportMode.PARTIAL_SSE),
    TransportCase("TRUNCATED_CHUNKED", TransportMode.TRUNCATED_CHUNKED),
    TransportCase("WEBSOCKET_REJECT", TransportMode.WEBSOCKET_REJECT),
    TransportCase("PROXY_REJECT", TransportMode.PROXY_REJECT),
    TransportCase("NO_RESPONSE", TransportMode.NO_RESPONSE),
    TransportCase("STARTUP_INVALID_CONFIG", TransportMode.STARTUP_INVALID_CONFIG),
)


class _BoundedCapture:
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

    def take(self) -> tuple[bytearray, bool]:
        raw = self._buffer
        self._buffer = bytearray()
        return raw, self._oversize


class _LoopbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def _assert_loopback_only() -> None:
    interfaces = {name for _index, name in socket.if_nameindex()}
    if interfaces != {"lo"}:
        raise RuntimeError("network_not_isolated")


def _case_state(case: TransportCase) -> tuple[Path, Path]:
    state_root = Path("/tmp/anygarden-smoke-transport") / case.mode.value
    home = state_root / "home"
    codex_home = state_root / "codex"
    for path in (home, codex_home):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    return home, codex_home


def _child_env(case: TransportCase, *, proxy_url: str | None = None) -> dict[str, str]:
    home, codex_home = _case_state(case)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "OPENAI_API_KEY": STUB_CREDENTIAL,
        "LANG": "C.UTF-8",
        "NO_PROXY": "",
        "no_proxy": "",
    }
    if proxy_url is not None:
        env.update(
            {
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
            }
        )
    return env


def _command_with_base_url(base_url: str) -> list[str]:
    command = smoke.build_command(STUB_MODEL)
    model_index = command.index("-m")
    return [
        *command[:model_index],
        "-c",
        f"openai_base_url={json.dumps(base_url)}",
        *command[model_index:],
    ]


def _startup_invalid_command() -> list[str]:
    command = smoke.build_command(STUB_MODEL)
    model_index = command.index("-m")
    return [
        *command[:model_index],
        "-c",
        "sandbox_mode=definitely_invalid",
        *command[model_index:],
    ]


def _close_transport(handler: http.server.BaseHTTPRequestHandler) -> None:
    handler.close_connection = True
    try:
        handler.connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    handler.connection.close()


def _send_fixed_response(
    handler: http.server.BaseHTTPRequestHandler,
    *,
    status: int,
    content_type: str,
    body: bytes,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if body:
        handler.wfile.write(body)


def _make_handler(
    case: TransportCase, audit: RequestAudit
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            audit.websocket_requests += 1
            audit.request_path_valid = (
                audit.request_path_valid and self.path == RESPONSES_PATH
            )
            if case.mode == TransportMode.WEBSOCKET_REJECT:
                _send_fixed_response(
                    self,
                    status=403,
                    content_type="text/plain",
                    body=b"",
                )
                return
            _send_fixed_response(
                self,
                status=501,
                content_type="text/plain",
                body=b"",
            )

        def do_CONNECT(self) -> None:
            audit.proxy_connects += 1
            _send_fixed_response(
                self,
                status=502,
                content_type="text/plain",
                body=b"",
            )

        def do_POST(self) -> None:
            audit.post_requests += 1
            audit.request_path_valid = (
                audit.request_path_valid and self.path == RESPONSES_PATH
            )
            remaining = int(self.headers.get("Content-Length", "0"))
            while remaining > 0:
                chunk = self.rfile.read(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)

            if case.mode == TransportMode.DELAYED_CLOSE:
                if audit.post_requests == 1:
                    time.sleep(DELAY_SECONDS)
                _close_transport(self)
                return
            if case.mode == TransportMode.DELAYED_EMPTY_SSE:
                if audit.post_requests == 1:
                    time.sleep(DELAY_SECONDS)
                _send_fixed_response(
                    self,
                    status=200,
                    content_type="text/event-stream",
                    body=b"",
                )
                return
            if case.mode == TransportMode.NO_RESPONSE:
                time.sleep(NO_RESPONSE_SECONDS)
                _close_transport(self)
                return
            if case.mode == TransportMode.MALFORMED_SSE:
                _send_fixed_response(
                    self,
                    status=200,
                    content_type="text/event-stream",
                    body=b"event: response.created\ndata: {\n\n",
                )
                return
            if case.mode == TransportMode.PARTIAL_SSE:
                _send_fixed_response(
                    self,
                    status=200,
                    content_type="text/event-stream",
                    body=(
                        b"event: response.created\n"
                        b'data: {"type":"response.created","response":'
                        b'{"id":"resp_stub","status":"in_progress"}}\n\n'
                    ),
                )
                return
            if case.mode == TransportMode.TRUNCATED_CHUNKED:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"40\r\nevent: response.created\ndata: {")
                self.wfile.flush()
                _close_transport(self)
                return
            if case.mode == TransportMode.WEBSOCKET_REJECT:
                _send_fixed_response(
                    self,
                    status=200,
                    content_type="text/event-stream",
                    body=b"",
                )
                return
            raise RuntimeError("unexpected_post_mode")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _execute(
    command: Sequence[str], env: Mapping[str, str]
) -> tuple[str, int, bytearray, bool, bytearray, bool]:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
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
        proc.wait(timeout=smoke.HARD_TIMEOUT_SECONDS)
        exit_state = "ZERO" if proc.returncode == 0 else "NONZERO"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=smoke.TERM_GRACE_SECONDS)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        exit_state = "TIMEOUT"
    stdout_thread.join()
    stderr_thread.join()
    stdout, stdout_oversize = stdout_capture.take()
    stderr, stderr_oversize = stderr_capture.take()
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    return (
        exit_state,
        duration_ms,
        stdout,
        stdout_oversize,
        stderr,
        stderr_oversize,
    )


def _historical_shape(raw_empty: bool, nonzero: bool, timing: bool) -> str:
    matches = sum((raw_empty, nonzero, timing))
    if matches == 3:
        return "MATCH"
    if matches == 0:
        return "MISMATCH"
    return f"PARTIAL_{matches}_OF_3"


def _closed_failure(
    *,
    exit_state: str,
    stdout: bytearray,
    stderr: bytearray,
    stderr_oversize: bool,
) -> tuple[str, str, str, str]:
    stdout_category, stdout_state = smoke.classify_failure_observation(bytes(stdout))
    stderr_category, stderr_state = smoke.classify_stderr_observation(
        stderr, oversize=stderr_oversize
    )
    if exit_state != "NONZERO":
        return (
            smoke.FAILURE_CATEGORY_NOT_APPLICABLE,
            "NOT_APPLICABLE",
            stdout_state,
            stderr_state,
        )
    if stdout_state in {"SINGLE_FAILURE_EVENT", "TERMINAL_FAILURE"}:
        return stdout_category, "STDOUT_TERMINAL", stdout_state, stderr_state
    if stdout_state in {"EMPTY", "NON_FAILURE_OUTPUT"}:
        if stderr_state == "SINGLE_FAILURE_SIGNAL":
            return stderr_category, "STDERR_ALLOWLIST", stdout_state, stderr_state
        if stdout_state == "EMPTY" and stderr_state == "EMPTY":
            return (
                smoke.FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT,
                "EMPTY_BOTH",
                stdout_state,
                stderr_state,
            )
    return smoke.FAILURE_CATEGORY_UNKNOWN, "UNKNOWN", stdout_state, stderr_state


def _observe(
    case: TransportCase,
    audit: RequestAudit,
    *,
    exit_state: str,
    duration_ms: int,
    stdout: bytearray,
    stdout_oversize: bool,
    stderr: bytearray,
    stderr_oversize: bool,
) -> TransportObservation:
    failure_category, category_source, stdout_state, stderr_state = _closed_failure(
        exit_state=exit_state,
        stdout=stdout,
        stderr=stderr,
        stderr_oversize=stderr_oversize,
    )
    raw_empty = not stdout
    nonzero = exit_state == "NONZERO"
    timing = HISTORICAL_DURATION_MIN_MS <= duration_ms <= HISTORICAL_DURATION_MAX_MS
    observation = TransportObservation(
        case=case.name,
        mode=case.mode.value,
        exit_state=exit_state,
        duration_ms=duration_ms,
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        stdout_state=stdout_state,
        stderr_state=stderr_state,
        failure_category=failure_category,
        category_source=category_source,
        websocket_requests=audit.websocket_requests,
        post_requests=audit.post_requests,
        proxy_connects=audit.proxy_connects,
        request_path_valid=audit.request_path_valid,
        raw_stdout_empty=raw_empty,
        nonzero_exit=nonzero,
        duration_near_historical=timing,
        historical_shape=_historical_shape(raw_empty, nonzero, timing),
        stdout_oversize=stdout_oversize,
        stderr_oversize=stderr_oversize,
    )
    del stdout
    del stderr
    return observation


def run_case(case: TransportCase) -> TransportObservation:
    audit = RequestAudit()
    server: _LoopbackServer | None = None
    server_thread: threading.Thread | None = None
    proxy_url: str | None = None
    if case.mode == TransportMode.STARTUP_INVALID_CONFIG:
        command = _startup_invalid_command()
    else:
        server = _LoopbackServer(("127.0.0.1", 0), _make_handler(case, audit))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        port = int(server.server_address[1])
        if case.mode == TransportMode.PROXY_REJECT:
            proxy_url = f"http://127.0.0.1:{port}"
            command = _command_with_base_url("https://provider.invalid/v1")
        else:
            command = _command_with_base_url(f"http://127.0.0.1:{port}/v1")
    try:
        result = _execute(command, _child_env(case, proxy_url=proxy_url))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join()
    return _observe(
        case,
        audit,
        exit_state=result[0],
        duration_ms=result[1],
        stdout=result[2],
        stdout_oversize=result[3],
        stderr=result[4],
        stderr_oversize=result[5],
    )


def _payload(observations: Sequence[TransportObservation]) -> dict[str, object]:
    return {
        "network": "LOOPBACK_ONLY",
        "credential": "STUB_ONLY",
        "provider_calls": 0,
        "historical_shape_is_hypothesis": True,
        "duration_window_ms": [
            HISTORICAL_DURATION_MIN_MS,
            HISTORICAL_DURATION_MAX_MS,
        ],
        "cases": [asdict(observation) for observation in observations],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--case",
        action="append",
        choices=[case.name for case in CASES],
        dest="case_names",
    )
    args = parser.parse_args()
    _assert_loopback_only()
    selected = (
        CASES
        if not args.case_names
        else tuple(case for case in CASES if case.name in set(args.case_names))
    )
    observations = [run_case(case) for case in selected]
    payload = _payload(observations)
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for observation in observations:
            print(
                f"{observation.case}: shape={observation.historical_shape} "
                f"exit={observation.exit_state} duration_ms={observation.duration_ms} "
                f"stdout={observation.stdout_state}/{observation.stdout_bytes} "
                f"stderr={observation.stderr_state}/{observation.stderr_bytes} "
                f"category={observation.failure_category} "
                f"requests={observation.websocket_requests}/"
                f"{observation.post_requests}/{observation.proxy_connects}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
