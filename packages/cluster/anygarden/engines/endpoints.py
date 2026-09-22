"""Local agent endpoint policy and encrypted credential resolution.

This module accepts local DB rows, never delegation command payloads.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, EngineCredential
from anygarden.engines.validation import PROVIDER_PATTERN

CONFIG_KEY = "AG_ENGINE_ENDPOINT_CONFIG"
INPUT_KEY = "AG_ENGINE_ENDPOINT_KEY"


class EndpointConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    provider: str = Field(pattern=PROVIDER_PATTERN, min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    api_protocol: str = Field(pattern=r"^(responses|chat-completions)$")
    credential_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")

    @model_validator(mode="after")
    def validate_url(self) -> EndpointConfiguration:
        if any(ord(c) < 33 for c in self.base_url) or "\\" in self.base_url:
            raise ValueError("Invalid endpoint URL")
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Endpoint URL cannot contain credentials, query or fragment"
            )
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Invalid endpoint port")
        if any(ord(c) < 32 for c in self.model):
            raise ValueError("Invalid model ID")
        return self

    def validate_engine(self, engine: str) -> None:
        if engine not in {"codex-cli", "pi-cli"} or (
            engine == "codex-cli" and self.api_protocol != "responses"
        ):
            raise ValueError(
                "Direct Codex endpoints require Responses; Pi also supports Chat Completions"
            )


def configuration_for(agent: Agent) -> EndpointConfiguration | None:
    if not agent.base_url:
        if agent.api_protocol or agent.credential_ref:
            raise ValueError("Incomplete endpoint configuration")
        return None
    config = EndpointConfiguration(
        provider=agent.provider,
        model=agent.model,
        base_url=agent.base_url,
        api_protocol=agent.api_protocol,
        credential_ref=agent.credential_ref,
    )
    config.validate_engine(agent.engine)
    return config


async def credential_for(db: AsyncSession, agent: Agent, ref: str) -> EngineCredential:
    row = await db.get(EngineCredential, ref)
    if row is None or row.agent_id != agent.id or row.engine != agent.engine:
        raise ValueError(
            "Credential reference is unavailable for this agent and engine"
        )
    return row


async def build_direct_engine_secrets(
    db: AsyncSession, agent: Agent, secrets_service
) -> dict[str, str]:
    config = configuration_for(agent)
    if config is None:
        return {}
    payload = config.model_dump()
    payload["credential_revision"] = 0
    result: dict[str, str] = {}
    if config.credential_ref:
        row = await credential_for(db, agent, config.credential_ref)
        if secrets_service is None:
            raise ValueError("Endpoint credential encryption service is unavailable")
        try:
            value = secrets_service.decrypt_dict(row.encrypted_value)["v"]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError(
                "Endpoint credential cannot be loaded; replace it in agent settings"
            ) from None
        result[INPUT_KEY] = value
        payload["credential_revision"] = row.revision
    result[CONFIG_KEY] = json.dumps(payload, sort_keys=True)
    return result
