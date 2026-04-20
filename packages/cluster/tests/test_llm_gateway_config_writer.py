"""Tests for :mod:`doorae.llm_gateway.config_writer` (#197).

``render_config`` is pure — one ``list[LLMGatewayModel]`` in, one
yaml string out — so the tests are just input/output snapshots plus
a disabled-row filter and a secret-leak assertion. No DB, no file I/O.
"""

from __future__ import annotations

import yaml

from doorae.db.models import LLMGatewayModel
from doorae.llm_gateway.config_writer import config_hash, render_config


def _model(
    *,
    name: str = "claude-sonnet-4-6",
    provider: str = "anthropic",
    upstream: str = "anthropic/claude-sonnet-4-6",
    key_ref: str = "ANTHROPIC_API_KEY",
    extra: dict | None = None,
    enabled: bool = True,
) -> LLMGatewayModel:
    """Build an in-memory model row without going through SQLAlchemy."""
    row = LLMGatewayModel(
        model_name=name,
        provider=provider,
        upstream_model=upstream,
        api_key_ref=key_ref,
        extra_params=extra,
        enabled=enabled,
    )
    return row


class TestRenderConfig:
    def test_single_model_produces_valid_litellm_yaml(self) -> None:
        text = render_config([_model()])
        parsed = yaml.safe_load(text)

        assert "model_list" in parsed
        assert len(parsed["model_list"]) == 1
        entry = parsed["model_list"][0]
        assert entry["model_name"] == "claude-sonnet-4-6"
        assert entry["litellm_params"]["model"] == "anthropic/claude-sonnet-4-6"
        # Key is a reference to an env var, not a value
        assert entry["litellm_params"]["api_key"] == (
            "os.environ/DOORAE_LITELLM_ANTHROPIC_API_KEY"
        )

    def test_general_settings_references_master_key_env(self) -> None:
        text = render_config([_model()])
        parsed = yaml.safe_load(text)

        # general_settings.master_key must be an env reference too —
        # the supervisor puts the actual value in DOORAE_LITELLM_MASTER_KEY
        # at spawn time.
        master = parsed["general_settings"]["master_key"]
        assert master == "os.environ/DOORAE_LITELLM_MASTER_KEY"
        # Stateless posture — spend logs table would otherwise require
        # a Postgres schema LiteLLM migrates itself.
        assert parsed["general_settings"]["disable_spend_logs"] is True

    def test_disabled_models_are_filtered(self) -> None:
        models = [
            _model(name="claude-sonnet-4-6", enabled=True),
            _model(
                name="gpt-5.4",
                provider="openai",
                upstream="openai/gpt-5.4",
                key_ref="OPENAI_API_KEY",
                enabled=False,
            ),
        ]
        parsed = yaml.safe_load(render_config(models))

        names = [m["model_name"] for m in parsed["model_list"]]
        assert names == ["claude-sonnet-4-6"]

    def test_extra_params_are_passed_through(self) -> None:
        text = render_config(
            [_model(extra={"temperature": 0.2, "max_tokens": 4096})]
        )
        params = yaml.safe_load(text)["model_list"][0]["litellm_params"]

        assert params["temperature"] == 0.2
        assert params["max_tokens"] == 4096
        # Never collide with the core fields
        assert params["model"] == "anthropic/claude-sonnet-4-6"

    def test_empty_model_list_is_valid(self) -> None:
        # LiteLLM boots fine with ``model_list: []`` and answers
        # "model not found" to requests. See §12.5.
        parsed = yaml.safe_load(render_config([]))
        assert parsed["model_list"] == []

    def test_no_plaintext_secrets_in_output(self) -> None:
        """Sanity check — a raw API key must not appear in the yaml.

        Guards against a future refactor that accidentally joins the
        secret table into the renderer and leaks values into the file.
        """
        rendered = render_config(
            [
                _model(key_ref="ANTHROPIC_API_KEY"),
                _model(
                    name="gpt-5.4",
                    provider="openai",
                    upstream="openai/gpt-5.4",
                    key_ref="OPENAI_API_KEY",
                ),
            ]
        )
        # Common live-key prefixes — if any appear, something went wrong.
        for needle in ("sk-ant-", "sk-proj-", "sk-or-", "AKIA"):
            assert needle not in rendered


class TestConfigHash:
    def test_stable_across_calls(self) -> None:
        models = [_model()]
        a = config_hash(render_config(models))
        b = config_hash(render_config(models))
        assert a == b

    def test_changes_when_model_list_changes(self) -> None:
        base = config_hash(render_config([_model()]))
        added = config_hash(
            render_config(
                [
                    _model(),
                    _model(name="gpt-5.4", upstream="openai/gpt-5.4"),
                ]
            )
        )
        assert base != added

    def test_short_form_is_hex_digest_prefix(self) -> None:
        # 16 hex chars — enough for the Status panel to display
        # without wrapping, not so short that collisions become likely.
        h = config_hash("hello")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)
