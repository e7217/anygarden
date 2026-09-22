"""Startup configuration no longer discovers or installs a gateway binary."""

from anygarden.cli import main
from anygarden.config import AnygardenSettings
from click.testing import CliRunner


def test_removed_settings_and_init_do_not_probe_binary(tmp_path, monkeypatch):
    assert not any(
        name.startswith("llm_gateway_") for name in AnygardenSettings.model_fields
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANYGARDEN_DATA_DIR", str(tmp_path / "data"))

    def no_binary_probe(*args, **kwargs):
        raise AssertionError("init must not discover a removed gateway binary")

    monkeypatch.setattr("shutil.which", no_binary_probe)
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert "Initialization complete" in result.output
    assert "litellm" not in result.output.lower()
