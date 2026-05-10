"""Tests for :mod:`doorae.llm_gateway.bootstrap` (#197).

The bootstrap module wires the supervisor's injectable hooks to their
production implementations — most notably ``_build_spawn_params_factory``
which reads DB state, decrypts secrets, writes ``litellm.yaml`` to disk,
and returns the ``_SpawnParams`` the supervisor hands to its ``spawn_fn``.

These tests exercise only the factory: given a populated DB and a
Fernet-backed secrets wrapper, the child env it produces carries the
master key, every decrypted secret under ``DOORAE_LITELLM_<name>``, and
the ``OLLAMA_DUMMY`` placeholder local providers depend on (#249).
"""

from __future__ import annotations

import secrets as _stdlib_secrets
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base, LLMGatewayModel, LLMGatewaySecret
from doorae.llm_gateway.bootstrap import (
    _build_health_probe,
    _build_spawn_params_factory,
)
from doorae.mcp_templates.encryption import MCPSecrets


@pytest_asyncio.fixture()
async def env(tmp_path) -> AsyncIterator[dict]:
    fernet_key = Fernet.generate_key().decode("ascii")
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=_stdlib_secrets.token_urlsafe(32),
        log_level="DEBUG",
        mcp_secrets_key=fernet_key,
        llm_gateway_config_path=str(tmp_path / "litellm.yaml"),
        llm_gateway_port=4001,
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    secrets_svc = MCPSecrets.from_config_key(fernet_key, dev_mode=False)

    yield {
        "config": config,
        "factory": factory,
        "secrets": secrets_svc,
        "config_path": Path(config.llm_gateway_config_path),
    }

    await engine.dispose()


async def test_child_env_always_carries_ollama_dummy_placeholder(env) -> None:
    """로컬 provider(api_key_ref='OLLAMA_DUMMY')가 참조하는 env var을
    supervisor child_env가 항상 제공해야 한다.

    비밀이 하나도 없는 DB 상태라도 ``DOORAE_LITELLM_OLLAMA_DUMMY`` 는
    ``sk-local`` 로 주입된다. 이 placeholder는 LiteLLM이 Ollama 호출
    시 실제로 사용하지 않지만, yaml의 ``os.environ/DOORAE_LITELLM_OLLAMA_DUMMY``
    참조가 resolve 될 수 있도록 env에 반드시 있어야 한다.
    """
    factory = _build_spawn_params_factory(
        env["config"], env["factory"], env["secrets"], master_key="sk-master"
    )
    params = await factory()

    assert params.child_env["DOORAE_LITELLM_OLLAMA_DUMMY"] == "sk-local"
    assert params.child_env["DOORAE_LITELLM_MASTER_KEY"] == "sk-master"
    assert params.master_key == "sk-master"


async def test_health_probe_uses_litellm_liveliness_endpoint() -> None:
    """LiteLLM's aggregate health route requires auth; probe liveness."""

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, float]] = []

        async def get(self, url: str, timeout: float):
            self.calls.append((url, timeout))

            class Response:
                status_code = 200

            return Response()

    client = FakeClient()
    probe = _build_health_probe(client)  # type: ignore[arg-type]

    assert await probe(4001) is True
    assert client.calls == [
        ("http://127.0.0.1:4001/health/liveliness", 1.0),
    ]


async def test_health_probe_loops_until_success() -> None:
    """#362 — probe must keep retrying past the old 9s deadline.

    Pre-#362 the inner loop returned False after 9 seconds, which
    capped the supervisor's effective health timeout regardless of
    its own ``health_timeout`` setting. After #362 the inner loop
    has no deadline; the supervisor's ``asyncio.wait_for`` is the
    sole timeout authority. This test simulates 50 connect-error
    retries (well past 9s of polling) followed by a 200, and
    expects ``True``.
    """
    import httpx

    class FlakyClient:
        def __init__(self) -> None:
            self.attempts = 0

        async def get(self, url: str, timeout: float):
            self.attempts += 1
            if self.attempts < 50:
                # Simulate the connect-failure path that fires while
                # litellm is still binding its socket.
                raise httpx.ConnectError("not yet bound")

            class Response:
                status_code = 200

            return Response()

    client = FlakyClient()
    probe = _build_health_probe(client)  # type: ignore[arg-type]

    assert await probe(4001) is True
    assert client.attempts == 50


async def test_supervisor_timeout_is_the_authority() -> None:
    """If the supervisor wraps the probe in ``wait_for(timeout=...)``
    and the probe never succeeds, the timeout fires from the outer
    layer, not from any internal probe deadline.

    Locks the contract documented in the probe docstring: termination
    lives on the supervisor side. A future regression that re-adds an
    inner deadline shorter than ``wait_for`` would cause the outer
    timeout to never fire — that's exactly what #362 set out to fix.
    """
    import asyncio
    import httpx

    class StuckClient:
        async def get(self, url: str, timeout: float):
            raise httpx.ConnectError("never ready")

    probe = _build_health_probe(StuckClient())  # type: ignore[arg-type]

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(probe(4001), timeout=0.5)


async def test_secret_rows_merge_with_placeholder_in_child_env(env) -> None:
    """관리자가 등록한 실제 비밀은 placeholder와 공존한다.

    Anthropic/OpenAI 같이 cloud provider용 비밀을 넣어도 OLLAMA_DUMMY
    는 덮이지 않고 각자 별도 키로 유지된다.
    """
    async with env["factory"]() as db:
        encrypted = env["secrets"].encrypt_dict({"v": "sk-ant-real"})
        db.add(
            LLMGatewaySecret(
                env_var_name="ANTHROPIC_API_KEY",
                encrypted_value=encrypted,
            )
        )
        await db.commit()

    factory = _build_spawn_params_factory(
        env["config"], env["factory"], env["secrets"], master_key="sk-master"
    )
    params = await factory()

    assert params.child_env["DOORAE_LITELLM_ANTHROPIC_API_KEY"] == "sk-ant-real"
    # Placeholder still present — cloud secret doesn't shadow it.
    assert params.child_env["DOORAE_LITELLM_OLLAMA_DUMMY"] == "sk-local"


async def test_rendered_yaml_written_to_config_path(env) -> None:
    """팩토리가 yaml을 디스크에 atomic 하게 쓰는지 확인.

    bootstrap의 책임 중 하나이므로 child_env 검증과 함께 묶어 둔다.
    Ollama 모델 한 개 + api_base 를 넣었을 때 렌더된 yaml이
    ``DOORAE_LITELLM_OLLAMA_DUMMY`` 를 참조하는지도 확인.
    """
    async with env["factory"]() as db:
        db.add(
            LLMGatewayModel(
                model_name="qwen3-remote",
                provider="ollama",
                upstream_model="ollama/qwen3-coder:30b",
                api_key_ref="OLLAMA_DUMMY",
                extra_params={"api_base": "http://10.0.0.5:11434"},
                enabled=True,
            )
        )
        await db.commit()

    factory = _build_spawn_params_factory(
        env["config"], env["factory"], env["secrets"], master_key="sk-master"
    )
    await factory()

    text = env["config_path"].read_text()
    # Provider rewritten by config_writer (`ollama/` → `ollama_chat/`)
    # so tool-using agents avoid the legacy ``format: json`` clamp.
    # See ``_rewrite_ollama_provider`` in config_writer.py.
    assert "ollama_chat/qwen3-coder:30b" in text
    assert "http://10.0.0.5:11434" in text
    assert "os.environ/DOORAE_LITELLM_OLLAMA_DUMMY" in text
