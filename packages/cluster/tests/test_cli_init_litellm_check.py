"""``anygarden init`` litellm pre-flight check (#406).

``init`` is the first command a fresh ``uvx --from "anygarden[server]"``
user runs. When the LLM Gateway binary (``litellm[proxy]``) isn't on PATH,
init must print an actionable install hint — but stay non-fatal, since
users who never enable the gateway shouldn't see init fail.
"""

from __future__ import annotations

from click.testing import CliRunner

from anygarden.cli import main


def test_init_warns_when_litellm_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # No litellm anywhere on PATH.
    monkeypatch.setattr("shutil.which", lambda _name: None)

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0  # non-fatal
    assert "uv tool install 'litellm[proxy]'" in result.output
    # Init still does its real job.
    assert "Initialization complete." in result.output


def test_init_silent_when_litellm_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # litellm resolves on PATH -> no hint.
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/litellm")

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0
    assert "litellm proxy CLI not found" not in result.output
    assert "uv tool install" not in result.output
    assert "Initialization complete." in result.output


def test_init_probes_binary_override(tmp_path, monkeypatch) -> None:
    """The probe must honour ANYGARDEN_LLM_GATEWAY_BINARY so init agrees
    with what the supervisor actually spawns."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANYGARDEN_LLM_GATEWAY_BINARY", "/custom/litellm")
    seen: list[str] = []

    def fake_which(name: str):
        seen.append(name)
        return None

    monkeypatch.setattr("shutil.which", fake_which)

    result = CliRunner().invoke(main, ["init"])

    assert result.exit_code == 0
    assert "/custom/litellm" in seen
