"""Local agent endpoint policy and encrypted credential resolution.

This module accepts local DB rows, never delegation command payloads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, EngineCredential
from anygarden.engines.validation import PROVIDER_PATTERN

CONFIG_KEY = "AG_ENGINE_ENDPOINT_CONFIG"
INPUT_KEY = "AG_ENGINE_ENDPOINT_KEY"


def validate_base_url(base_url: object) -> str:
    """Shared direct-endpoint URL policy (#679, reused by the #685 probe).

    HTTP(S) only, a host, no userinfo/query/fragment, no whitespace or
    control characters. Raises ``ValueError`` with a message that never
    reflects the value (it may carry credentials).
    """
    if (
        not isinstance(base_url, str)
        or not base_url
        or len(base_url) > 2048
        or any(ord(c) < 33 for c in base_url)
        or "\\" in base_url
    ):
        raise ValueError("Invalid endpoint URL")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid endpoint URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Endpoint URL cannot contain credentials, query or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid endpoint port")
    return base_url


class EndpointConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())
    provider: str = Field(pattern=PROVIDER_PATTERN, min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    base_url: str = Field(min_length=1, max_length=2048)
    api_protocol: str = Field(pattern=r"^(responses|chat-completions)$")
    credential_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,64}$")

    @model_validator(mode="after")
    def validate_url(self) -> EndpointConfiguration:
        validate_base_url(self.base_url)
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


# ── Model discovery probe (#685) ────────────────────────────────────
#
# Admin-only helper that lists ``GET {base_url}/models`` so operators can
# pick a served model ID instead of curling it by hand. The request runs
# from the server, not from the machine that will call the model, so the
# result means "reachable from the server". SSRF / secret-leak surface is
# kept small: validated URL, GET only, no redirects, no proxy env, short
# timeout, capped body, fixed error messages (never the upstream body or
# exception text, which may echo credentials or internal hostnames).

MODEL_PROBE_TIMEOUT = 5.0
MODEL_PROBE_MAX_BYTES = 1_048_576
MODEL_PROBE_MAX_MODELS = 1000


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    max_model_len: int | None = None


class ModelProbeError(Exception):
    """Probe failure with an operator-safe, value-free message."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def parse_model_list(payload: object) -> list[DiscoveredModel]:
    """Parse an OpenAI-compatible ``/models`` body (vLLM, llama.cpp, Ollama)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ModelProbeError("The model server response is not an OpenAI-compatible model list")
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if (
            not isinstance(model_id, str)
            or not model_id
            or len(model_id) > 256
            or any(ord(c) < 32 for c in model_id)
            or model_id in seen
        ):
            continue
        length = item.get("max_model_len")
        seen.add(model_id)
        models.append(
            DiscoveredModel(
                model_id, length if type(length) is int and length > 0 else None
            )
        )
        if len(models) >= MODEL_PROBE_MAX_MODELS:
            break
    return models


async def probe_models(
    base_url: str,
    api_key: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[DiscoveredModel]:
    validate_base_url(base_url)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(
            timeout=MODEL_PROBE_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code in (401, 403):
                    raise ModelProbeError(
                        f"The model server rejected the credentials (HTTP {response.status_code})"
                    )
                if response.status_code != 200:
                    raise ModelProbeError(
                        f"The model server returned HTTP {response.status_code} for /models"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MODEL_PROBE_MAX_BYTES:
                        raise ModelProbeError("The model list response is too large")
    except httpx.TimeoutException:
        raise ModelProbeError("The model server did not respond in time", 504) from None
    except httpx.HTTPError:
        raise ModelProbeError(
            "Could not reach the model server from the AnyGarden server"
        ) from None
    try:
        payload = json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError):
        raise ModelProbeError(
            "The model server response is not an OpenAI-compatible model list"
        ) from None
    return parse_model_list(payload)
