from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
SMOKE_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_gate", SCRIPTS / "engine_smoke_gate.py"
)
assert SMOKE_SPEC is not None and SMOKE_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SMOKE_SPEC)
sys.modules[SMOKE_SPEC.name] = smoke
SMOKE_SPEC.loader.exec_module(smoke)

TRANSPORT_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_transport_validation",
    SCRIPTS / "engine_smoke_transport_validation.py",
)
assert TRANSPORT_SPEC is not None and TRANSPORT_SPEC.loader is not None
transport = importlib.util.module_from_spec(TRANSPORT_SPEC)
sys.modules[TRANSPORT_SPEC.name] = transport
TRANSPORT_SPEC.loader.exec_module(transport)


def test_transport_suite_covers_requested_axes() -> None:
    assert {case.mode for case in transport.CASES} == set(transport.TransportMode)
    assert {case.name for case in transport.CASES} == {
        "DELAYED_CLOSE",
        "DELAYED_EMPTY_SSE",
        "MALFORMED_SSE",
        "PARTIAL_SSE",
        "TRUNCATED_CHUNKED",
        "WEBSOCKET_REJECT",
        "PROXY_REJECT",
        "NO_RESPONSE",
        "STARTUP_INVALID_CONFIG",
    }


def test_shape_requires_all_three_components() -> None:
    assert transport._historical_shape(True, True, True) == "MATCH"
    assert transport._historical_shape(True, True, False) == "PARTIAL_2_OF_3"
    assert transport._historical_shape(True, False, False) == "PARTIAL_1_OF_3"
    assert transport._historical_shape(False, False, False) == "MISMATCH"


def test_observation_projects_closed_fields_and_discards_raw() -> None:
    case = transport.TransportCase(
        "DELAYED_CLOSE", transport.TransportMode.DELAYED_CLOSE
    )
    observation = transport._observe(
        case,
        transport.RequestAudit(websocket_requests=1, post_requests=6),
        exit_state="NONZERO",
        duration_ms=18_318,
        stdout=bytearray(),
        stdout_oversize=False,
        stderr=bytearray(),
        stderr_oversize=False,
    )
    assert observation.historical_shape == "MATCH"
    assert observation.failure_category == smoke.FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT
    assert observation.category_source == "EMPTY_BOTH"
    assert observation.stdout_bytes == 0
    payload = transport._payload([observation])
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["historical_shape_is_hypothesis"] is True
    for forbidden in (
        '"raw_stdout":',
        '"raw_stderr":',
        "endpoint",
        "request_body",
        transport.STUB_CREDENTIAL,
        transport.STUB_MODEL,
    ):
        assert forbidden not in serialized


def test_nonzero_terminal_stdout_is_only_partial_shape() -> None:
    case = transport.TransportCase(
        "MALFORMED_SSE", transport.TransportMode.MALFORMED_SSE
    )
    stdout = bytearray(
        b'{"type":"turn.failed","error":{"message":'
        b'"stream disconnected before completion:"}}\n'
    )
    observation = transport._observe(
        case,
        transport.RequestAudit(post_requests=6),
        exit_state="NONZERO",
        duration_ms=18_318,
        stdout=stdout,
        stdout_oversize=False,
        stderr=bytearray(b"common non-authoritative diagnostic"),
        stderr_oversize=False,
    )
    assert observation.historical_shape == "PARTIAL_2_OF_3"
    assert observation.failure_category == "UPSTREAM"
    assert observation.category_source == "STDOUT_TERMINAL"


def test_timeout_never_counts_as_nonzero_exit() -> None:
    case = transport.TransportCase("NO_RESPONSE", transport.TransportMode.NO_RESPONSE)
    observation = transport._observe(
        case,
        transport.RequestAudit(websocket_requests=1, post_requests=1),
        exit_state="TIMEOUT",
        duration_ms=60_000,
        stdout=bytearray(),
        stdout_oversize=False,
        stderr=bytearray(),
        stderr_oversize=False,
    )
    assert observation.nonzero_exit is False
    assert observation.failure_category == smoke.FAILURE_CATEGORY_NOT_APPLICABLE
    assert observation.historical_shape == "PARTIAL_1_OF_3"


def test_commands_preserve_fixed_smoke_flags() -> None:
    base = smoke.build_command(transport.STUB_MODEL)
    loopback = transport._command_with_base_url("http://127.0.0.1:12345/v1")
    invalid = transport._startup_invalid_command()
    for expected in (
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "read-only",
        "approval_policy=untrusted",
        "model_reasoning_effort=minimal",
    ):
        assert expected in base
        assert expected in loopback
        assert expected in invalid
    assert 'openai_base_url="http://127.0.0.1:12345/v1"' in loopback
    assert "sandbox_mode=definitely_invalid" in invalid


def test_proxy_environment_is_loopback_and_clears_bypass() -> None:
    case = transport.TransportCase("PROXY_REJECT", transport.TransportMode.PROXY_REJECT)
    env = transport._child_env(case, proxy_url="http://127.0.0.1:12345")
    assert env["HTTP_PROXY"] == "http://127.0.0.1:12345"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:12345"
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""
