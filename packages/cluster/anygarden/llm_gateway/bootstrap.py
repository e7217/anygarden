"""Production wiring for the LLM gateway (#197).

Translates the injectable hooks on :class:`LLMGatewaySupervisor`
(``spawn_fn`` / ``health_probe`` / ``spawn_params_factory``) to
real-world implementations that:

- Spawn the ``litellm`` binary via
  :func:`asyncio.create_subprocess_exec`, passing ``--config`` +
  ``--port`` + ``--host 127.0.0.1``.
- Probe ``GET http://127.0.0.1:<port>/health/liveliness`` via an
  :class:`httpx.AsyncClient`, polling until 2xx or timeout.
- Read the current gateway DB state, render ``litellm.yaml``, write it
  to disk, decrypt each referenced secret, and return a
  :class:`_SpawnParams` the supervisor passes straight through.

Tests exercise the components individually with injected fakes; this
module is the glue code that lives in ``app.py``'s ``lifespan``.
"""

from __future__ import annotations

import asyncio
import os
import secrets as _stdlib_secrets
from pathlib import Path
from typing import Any, Callable

import httpx
import structlog
from fastapi import FastAPI
from sqlalchemy import select

from anygarden_machine.safefs import secure_chmod

from anygarden.config import AnygardenSettings
from anygarden.db.models import LLMGatewayModel, LLMGatewaySecret
from anygarden.llm_gateway.config_writer import render_config
from anygarden.llm_gateway.supervisor import (
    LLMGatewaySupervisor,
    _SpawnParams,
)

logger = structlog.get_logger(__name__)


def _resolve_config_path(config: AnygardenSettings) -> Path:
    """Where to write the rendered ``litellm.yaml``."""
    if config.llm_gateway_config_path:
        return Path(config.llm_gateway_config_path)
    return Path.home() / ".anygarden" / "litellm.yaml"


async def _real_spawn(params: _SpawnParams, binary: str) -> Any:
    """Production ``spawn_fn`` — launches the real litellm binary.

    Inherits the server's env + the params' child_env (decrypted
    secrets, master key). Binds to 127.0.0.1 only so the subprocess
    is unreachable from the network — the reverse proxy is the only
    access path.
    """
    env = os.environ.copy()
    env.update(params.child_env)
    return await asyncio.create_subprocess_exec(
        binary,
        "--config", str(params.config_path),
        "--host", "127.0.0.1",
        "--port", str(params.port),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


def _build_health_probe(client: httpx.AsyncClient) -> Callable[[int], Any]:
    """Return a probe callable bound to a shared httpx client.

    Polls ``GET /health/liveliness`` until the litellm subprocess
    reports 2xx. Returns True on success.

    Termination contract: this coroutine **does not enforce its own
    timeout** — the caller (``LLMGatewaySupervisor._spawn_once``)
    wraps it in ``asyncio.wait_for(..., timeout=health_timeout)`` and
    a single source of truth on the deadline lives there. #362 —
    pre-fix this had a hardcoded 9s deadline that fired before
    supervisor's ``wait_for`` could grant the configured timeout, so
    raising the supervisor knob alone didn't help. Removing the
    inner deadline lets the supervisor's value (default 30s, config
    overridable) actually take effect.

    Connection errors during startup (when litellm is still binding)
    are expected, so they just retry instead of failing the whole
    probe.
    """

    async def probe(port: int) -> bool:
        url = f"http://127.0.0.1:{port}/health/liveliness"
        while True:
            try:
                resp = await client.get(url, timeout=1.0)
                if 200 <= resp.status_code < 300:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)

    return probe


def _build_spawn_params_factory(
    config: AnygardenSettings,
    session_factory: Any,
    mcp_secrets: Any,
    master_key: str,
) -> Callable[[], Any]:
    """Return a factory that reads current DB state → _SpawnParams.

    Called by the supervisor before every spawn (initial start and
    respawn after Apply). Reads the gateway model/secret tables,
    decrypts each referenced secret with the shared Fernet key, and
    writes the yaml to disk. Master key is constant for the server
    process's lifetime — regenerating per-spawn would break in-flight
    requests on every Apply.
    """
    config_path = _resolve_config_path(config)

    async def factory() -> _SpawnParams:
        async with session_factory() as db:
            models = (await db.execute(select(LLMGatewayModel))).scalars().all()
            secrets_rows = (
                await db.execute(select(LLMGatewaySecret))
            ).scalars().all()

        # Build child env: one ANYGARDEN_LITELLM_<name>=<plaintext> per
        # secret row, plus the master key. The yaml references these
        # names via ``os.environ/ANYGARDEN_LITELLM_...`` — live values
        # never land in the file on disk.
        # Each secret row stores a single-key dict so we can reuse
        # ``MCPSecrets.encrypt_dict``/``decrypt_dict`` (already battle-
        # tested for #124) without introducing a separate encryption
        # helper just for the gateway. The ``"v"`` key is arbitrary —
        # the writer on the admin API side uses the same convention.
        #
        # ``ANYGARDEN_LITELLM_OLLAMA_DUMMY`` is the placeholder referenced
        # by local-provider rows (Ollama / vLLM / custom) whose admin
        # left ``api_key_ref`` blank — the admin API normalises such
        # input to the ``OLLAMA_DUMMY`` sentinel, and config_writer
        # therefore renders ``api_key: os.environ/ANYGARDEN_LITELLM_OLLAMA_DUMMY``
        # into the yaml. LiteLLM ignores the value for Ollama, but we
        # still supply a stable string so the reference resolves and
        # no "missing env var" warning surfaces. A secret row also
        # named ``OLLAMA_DUMMY`` would overwrite this during the loop
        # below; that's acceptable (admin override semantics).
        child_env: dict[str, str] = {
            "ANYGARDEN_LITELLM_MASTER_KEY": master_key,
            "ANYGARDEN_LITELLM_OLLAMA_DUMMY": "sk-local",
        }
        for row in secrets_rows:
            try:
                payload = mcp_secrets.decrypt_dict(row.encrypted_value)
                plaintext = payload.get("v", "")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "llm_gateway.secret_decrypt_failed",
                    env_var=row.env_var_name,
                    error=str(exc),
                )
                continue
            if plaintext:
                child_env[f"ANYGARDEN_LITELLM_{row.env_var_name}"] = plaintext

        # Render + write yaml atomically.
        rendered = render_config(models)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = config_path.with_suffix(".yaml.tmp")
        tmp.write_text(rendered)
        tmp.replace(config_path)
        try:
            secure_chmod(config_path, 0o600)
        except OSError:
            pass

        return _SpawnParams(
            config_path=config_path,
            child_env=child_env,
            master_key=master_key,
            port=config.llm_gateway_port,
        )

    return factory


async def bootstrap_gateway(
    app: FastAPI,
    config: AnygardenSettings,
    session_factory: Any,
    mcp_secrets: Any,
) -> None:
    """Wire up the LLM gateway supervisor onto ``app.state``.

    Called from ``lifespan`` when ``config.llm_gateway_enabled`` is
    true. After return, the reverse proxy route is fully functional
    (assuming the supervisor reached RUNNING; it may be FAILED if
    litellm can't start, in which case the Status panel shows why).
    """
    master_key = f"sk-anygarden-{_stdlib_secrets.token_urlsafe(32)}"
    port = config.llm_gateway_port

    # Shared httpx client for both health probing and the reverse-proxy
    # relay. Reuses connections across requests — the subprocess is
    # local so pool size stays small.
    client = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=httpx.Timeout(60.0, connect=2.0),
    )
    app.state.llm_gateway_client = client

    supervisor = LLMGatewaySupervisor(
        spawn_params_factory=_build_spawn_params_factory(
            config, session_factory, mcp_secrets, master_key
        ),
        spawn_fn=_real_spawn,
        health_probe=_build_health_probe(client),
        # #362 — let the operator widen the boot grace period via
        # config when 30s isn't enough (cold disk, large model_list,
        # litellm minor-version startup regressions).
        health_timeout=config.llm_gateway_health_timeout_sec,
        # #364 — explicit binary path so PATH-shadowing by a bare
        # transitive ``litellm`` in the monorepo venv (pulled in by
        # openhands-sdk per #355) doesn't hijack the spawn. Default
        # ``"litellm"`` keeps PATH lookup behaviour for environments
        # that don't need the override.
        binary=config.llm_gateway_binary,
    )
    app.state.llm_gateway_supervisor = supervisor

    await supervisor.start()
    logger.info(
        "llm_gateway.bootstrapped",
        state=supervisor.state.value,
        port=port,
    )


async def shutdown_gateway(app: FastAPI) -> None:
    """Tear down the supervisor and close the shared httpx client."""
    supervisor = getattr(app.state, "llm_gateway_supervisor", None)
    if supervisor is not None:
        try:
            await supervisor.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_gateway.stop_failed", error=str(exc))
        app.state.llm_gateway_supervisor = None

    client: httpx.AsyncClient | None = getattr(
        app.state, "llm_gateway_client", None
    )
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_gateway.client_close_failed", error=str(exc))
        app.state.llm_gateway_client = None
