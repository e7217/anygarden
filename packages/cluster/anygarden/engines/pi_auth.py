"""Resolve agent-scoped Pi native credentials for private machine delivery."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, PiNativeCredential

CONFIG_KEY = "AG_PI_NATIVE_AUTH_CONFIG"
INPUT_KEY = "AG_PI_NATIVE_AUTH_KEY"
CAPABILITY = "pi_native_auth_v1"
MISSING_MESSAGE = "Pi provider credential is missing; add an API key in agent settings"


async def build_pi_native_engine_secrets(
    db: AsyncSession, agent: Agent, secrets_service
) -> dict[str, str]:
    if agent.engine != "pi-cli" or agent.base_url:
        return {}
    if not isinstance(agent.model, str) or not agent.model.strip():
        raise ValueError("Pi native model is missing; select a model in agent settings")
    row = await db.get(PiNativeCredential, agent.id)
    if row is None or row.provider != agent.provider:
        raise ValueError(MISSING_MESSAGE)
    if secrets_service is None:
        raise ValueError("Pi credential encryption service is unavailable")
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
            "Pi credential cannot be loaded; replace it in agent settings"
        ) from None
    return {
        CONFIG_KEY: json.dumps({"provider": row.provider, "revision": row.revision}),
        INPUT_KEY: value,
    }
