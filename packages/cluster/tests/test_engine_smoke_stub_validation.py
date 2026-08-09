from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_gate", SCRIPTS / "engine_smoke_gate.py"
)
assert SMOKE_SPEC is not None and SMOKE_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SMOKE_SPEC)
sys.modules[SMOKE_SPEC.name] = smoke
SMOKE_SPEC.loader.exec_module(smoke)

STUB_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_stub_validation",
    SCRIPTS / "engine_smoke_stub_validation.py",
)
assert STUB_SPEC is not None and STUB_SPEC.loader is not None
stub = importlib.util.module_from_spec(STUB_SPEC)
sys.modules[STUB_SPEC.name] = stub
STUB_SPEC.loader.exec_module(stub)


def test_cases_cover_fixed_wire_contract_and_isolated_state_dirs() -> None:
    assert {
        case.name: (case.status, case.expected_category) for case in stub.CASES
    } == {
        "AUTH_401": (401, "AUTH_REJECTED"),
        "MODEL_403": (403, "MODEL_ACCESS"),
        "MODEL_404": (404, "MODEL_ACCESS"),
        "EMPTY_200": (200, "UPSTREAM"),
        "MIXED_401_404": (401, "UNKNOWN"),
        "REPEATED_404": (404, "UNKNOWN"),
    }
    assert set(stub.CASE_STATE_DIRS) == {case.name for case in stub.CASES}
    assert len(set(stub.CASE_STATE_DIRS.values())) == len(stub.CASE_STATE_DIRS)
    assert stub.CANONICAL_CASE_NAMES == {
        "AUTH_401",
        "MODEL_403",
        "MODEL_404",
        "EMPTY_200",
    }
    assert stub.DERIVED_PATTERN_IDS == {
        "AUTH_401": "HTTP_401+INCORRECT_API_KEY",
        "MODEL_403": "HTTP_403+MODEL_ACCESS_COPY",
        "MODEL_404": "HTTP_404+MODEL_NOT_FOUND_COPY",
        "EMPTY_200": "STREAM_DISCONNECTED_BEFORE_COMPLETION",
    }


def test_stub_command_uses_production_command_and_supported_local_override() -> None:
    base_url = "http://127.0.0.1:12345/v1"
    production = smoke.build_command(stub.STUB_MODEL)
    command = stub._build_stub_command(base_url)
    model_index = production.index("-m")

    assert command == [
        *production[:model_index],
        "-c",
        f"openai_base_url={json.dumps(base_url)}",
        *production[model_index:],
    ]
    assert command.count("-c") == production.count("-c") + 1


def test_network_guard_requires_loopback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stub.socket, "if_nameindex", lambda: [(1, "lo")])
    stub._assert_loopback_only()

    monkeypatch.setattr(stub.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    with pytest.raises(RuntimeError, match="^network_not_isolated$"):
        stub._assert_loopback_only()


def test_stdout_projection_contains_only_event_type_and_closed_category() -> None:
    raw = (
        b'{"type":"error","message":"opaque retry detail"}\n'
        b'{"type":"turn.failed","error":{"message":"unexpected status '
        b"403 Forbidden: The requested model does not exist or you do not have "
        b'access to it"}}'
    )

    assert stub._project_stdout_failure_events(raw) == (
        "error:UNKNOWN",
        "turn.failed:MODEL_ACCESS",
    )


@pytest.mark.parametrize(
    ("case_name", "message"),
    [
        (
            "AUTH_401",
            "unexpected status 401 Unauthorized: Incorrect API key provided",
        ),
        (
            "MODEL_403",
            (
                "unexpected status 403 Forbidden: The requested model does not "
                "exist or you do not have access to it"
            ),
        ),
        (
            "MODEL_404",
            "unexpected status 404 Not Found: The requested model does not exist",
        ),
        (
            "EMPTY_200",
            (
                "stream disconnected before completion: stream closed before "
                "response.completed"
            ),
        ),
    ],
)
def test_derived_patterns_match_one_terminal_event(
    case_name: str, message: str
) -> None:
    raw = json.dumps(
        {"type": "turn.failed", "error": {"message": message}},
        separators=(",", ":"),
    ).encode()

    assert stub._matches_derived_pattern(case_name, raw) is True
    assert stub._matches_derived_pattern(case_name, raw + b"\n" + raw) is False


def test_stderr_structure_projects_common_fallback_without_category_authority() -> None:
    raw = (
        b"WARNING: proceeding, even though we could not create PATH aliases: "
        b'Refusing to create helper binaries under temporary dir "/tmp" '
        b'(codex_home: AbsolutePathBuf("/tmp/isolated/codex"))\n'
        b"2026-01-01T00:00:00Z ERROR "
        b"codex_api::endpoint::responses_websocket: failed to connect to "
        b"websocket: HTTP error: 501 Not Implemented, url: "
        b"ws://127.0.0.1:12345/v1/responses\n"
    )

    assert stub._project_stderr_structure(raw) == "WEBSOCKET_FALLBACK_ONLY"
    assert smoke.classify_stderr_observation(raw) == ("UNKNOWN", "UNRECOGNIZED")
    assert stub._project_stderr_structure(b"case-specific failure") == "OTHER"


@pytest.mark.parametrize(
    "raw",
    [
        (
            b"2026-01-01T00:00:00Z ERROR "
            b"codex_api::endpoint::responses_websocket: failed to connect to "
            b"websocket: HTTP error: 501 Not Implemented, url: "
            b"ws://127.0.0.1:12345/v1/responses case-specific-tail\n"
        ),
        (
            b"2026-01-01T00:00:00Z ERROR "
            b"codex_api::endpoint::responses_websocket: failed to connect to "
            b"websocket: HTTP error: 501 Not Implemented, url: "
            b"ws://192.0.2.1:12345/v1/responses\n"
        ),
        (
            b"2026-01-01T00:00:00Z ERROR "
            b"codex_api::endpoint::responses_websocket: failed to connect to "
            b"websocket: HTTP error: 501 Not Implemented, url: "
            b"ws://127.0.0.1:12345/v1/responses?extra=1\n"
        ),
        (
            b"WARNING: proceeding, even though we could not create PATH aliases: "
            b'Refusing to create helper binaries under temporary dir "/tmp" '
            b'(codex_home: AbsolutePathBuf("/tmp/isolated/codex")) extra\n'
        ),
    ],
    ids=("extra-tail", "non-loopback", "query", "warning-extra-tail"),
)
def test_stderr_structure_rejects_changed_or_extended_diagnostics(raw: bytes) -> None:
    assert stub._project_stderr_structure(raw) == "OTHER"


def test_payload_schema_cannot_emit_raw_output_or_dynamic_runtime_values() -> None:
    observation = stub.StubObservation(
        case="EMPTY_200",
        expected_category="UPSTREAM",
        matched=True,
        exit_state="NONZERO",
        request_count=6,
        request_path_valid=True,
        stdout_bytes=10,
        stderr_bytes=20,
        stdout_category="UPSTREAM",
        stdout_state="TERMINAL_FAILURE",
        stdout_failure_events=2,
        stdout_event_categories=("error:UPSTREAM", "turn.failed:UPSTREAM"),
        stderr_category="UNKNOWN",
        stderr_state="UNRECOGNIZED",
        category_source="STDOUT_TERMINAL",
        derived_pattern_id="STREAM_DISCONNECTED_BEFORE_COMPLETION",
        derived_pattern_matched=True,
        stderr_structure="WEBSOCKET_FALLBACK_ONLY",
        historical_canary_shape="MISMATCH",
        stdout_oversize=False,
        stderr_oversize=False,
    )

    payload = stub._build_payload([observation])
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["network"] == "LOOPBACK_ONLY"
    assert payload["credential"] == "STUB_ONLY"
    assert payload["provider_calls"] == 0
    assert payload["all_matched"] is True
    assert payload["cases"][0]["category_source"] == "STDOUT_TERMINAL"
    assert payload["cases"][0]["derived_pattern_matched"] is True
    assert payload["cases"][0]["stderr_structure"] == "WEBSOCKET_FALLBACK_ONLY"
    assert payload["cases"][0]["historical_canary_shape"] == "MISMATCH"
    assert "raw" not in encoded.casefold()
    assert "endpoint" not in encoded.casefold()
    assert "model" not in encoded.casefold()
    assert '"stdout":' not in encoded
    assert '"stderr":' not in encoded
