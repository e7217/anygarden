"""Only safe, unambiguous CLI failures become actionable codes."""

import pytest

from anygarden_agent.runtime.execution.failure_feedback import classify_failure


@pytest.mark.parametrize(
    ("engine", "message", "expected"),
    [
        ("codex-cli", "Not logged in. Run codex login", "ENGINE_AUTH_ERROR"),
        ("codex-cli", "unexpected status 401 Unauthorized: secret-key", "ENGINE_AUTH_ERROR"),
        ("pi-cli", "Authentication failed: secret-key", "ENGINE_AUTH_ERROR"),
        ("pi-cli", "Unknown provider: my-local", "PI_PROVIDER_ERROR"),
        ("pi-cli", "Provider my-local not found", "PI_PROVIDER_ERROR"),
        ("pi-cli", "401: secret-key", "ENGINE_AUTH_ERROR"),
        ("codex-cli", "403 Forbidden: model access denied", None),
        ("pi-cli", "unknown provider; authentication failed", None),
        ("pi-cli", "model not found", None),
        ("codex-cli", "DO-NOT-PUBLISH", None),
        ("codex-cli", "authentication failed" * 500, None),
        ("pi-cli", None, None),
        ("pi-cli", {"message": "authentication failed"}, None),
    ],
)
def test_classify_failure(engine, message, expected):
    assert classify_failure(engine, message) == expected
