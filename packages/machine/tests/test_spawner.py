"""Tests for agent subprocess spawner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anygarden_machine.spawner import (
    KILL_TIMEOUT,
    RunningAgent,
    SpawnManifest,
    Spawner,
)


@pytest.fixture
def spawner(tmp_path: Path) -> Spawner:
    """Create a Spawner with mock callbacks and an isolated agent dirs root.

    ``agent_dirs_root`` is redirected at ``tmp_path`` so the spawner's
    materialize step doesn't leak files into the developer's real
    ``~/.anygarden/agents/`` directory when tests run.
    """
    return Spawner(
        on_stopped=AsyncMock(),
        on_crashed=AsyncMock(),
        agent_dirs_root=tmp_path / "agents",
    )


@pytest.fixture
def spawn_msg() -> SpawnManifest:
    """A valid SpawnManifest."""
    return SpawnManifest(
        agent_id="agent-test-001",
        engine="claude-code",
        agent_token="secret-token-xyz",
        profile_yaml="name: test-agent\nmodel: claude-3",
        rooms=["room-alpha"],
        server_url="wss://localhost:8000/ws/agent",
    )


def _mock_proc(pid: int = 42) -> MagicMock:
    """Build a MagicMock subprocess with a properly async-compatible
    stdin. The spawner now writes the engine_secrets JSON payload to
    stdin via ``write`` → ``drain`` → ``close`` → ``wait_closed``, so
    naked ``MagicMock()`` procs fail on ``await drain()`` with a
    ``TypeError`` (#184 follow-up). Tests that don't care about the
    stdin payload still need the coroutine methods wired up.
    """
    proc = MagicMock()
    proc.pid = pid
    proc.wait = AsyncMock(return_value=0)
    proc.stderr = None
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    stdin.close = MagicMock()
    stdin.wait_closed = AsyncMock()
    proc.stdin = stdin
    return proc


class TestSpawnEnvSecrets:
    """#184 follow-up: engine_secrets must NOT end up in the agent
    process env (``/proc/self/environ``). They are delivered via
    stdin instead and the agent's ``anygarden_agent.secrets`` module
    stores them in private memory.
    """

    async def test_engine_secrets_absent_from_subprocess_env(
        self, spawner: Spawner
    ) -> None:
        """Security regression guard: the agent subprocess must never
        receive engine_secrets in its initial env — otherwise an LLM
        tool call can dump ``/proc/self/environ`` and exfiltrate
        every API key.
        """
        msg = SpawnManifest(
            agent_id="agent-secret",
            engine="claude-code",
            agent_token="tok-xyz",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            engine_secrets={"ANTHROPIC_API_KEY": "sk-shh", "OTHER": "v"},
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.write = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.close = MagicMock()
        mock_proc.stdin.wait_closed = AsyncMock()
        captured: dict = {}

        async def capture_exec(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            captured["stdin"] = kwargs.get("stdin")
            return mock_proc

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(msg)

        assert result.success is True
        env = captured["env"]
        assert env is not None
        assert "ANTHROPIC_API_KEY" not in env
        assert "OTHER" not in env
        # ANYGARDEN_TOKEN (agent identity) stays in env by design —
        # ``load_token`` on the agent side reads it from env.
        assert env["ANYGARDEN_TOKEN"] == "tok-xyz"
        # stdin PIPE must be requested so the secrets payload can be
        # written. A value of ``None`` would mean inherit, which would
        # both fail to deliver secrets and potentially leak the
        # daemon's stdin into the agent.
        assert captured["stdin"] == asyncio.subprocess.PIPE

    async def test_turn_timeout_sec_injected_into_env_when_set(
        self, spawner: Spawner
    ) -> None:
        """#493 — a per-agent turn timeout is exported as
        ``ANYGARDEN_AGENT_TURN_TIMEOUT_SEC`` so the engine adapter resolves it.
        """
        msg = SpawnManifest(
            agent_id="agent-tt",
            engine="claude-code",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            turn_timeout_sec=450,
        )
        captured: dict = {}

        async def capture_exec(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return _mock_proc()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(msg)

        assert result.success is True
        assert captured["env"]["ANYGARDEN_AGENT_TURN_TIMEOUT_SEC"] == "450"

    async def test_turn_timeout_sec_absent_when_unset(
        self, spawner: Spawner
    ) -> None:
        """#493 — no per-agent value → the env key is absent so the adapter
        falls back to the global env / hardcoded default.
        """
        msg = SpawnManifest(
            agent_id="agent-tt-none",
            engine="claude-code",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
        )
        captured: dict = {}

        async def capture_exec(*args, **kwargs):
            captured["env"] = kwargs.get("env")
            return _mock_proc()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(msg)

        assert result.success is True
        assert "ANYGARDEN_AGENT_TURN_TIMEOUT_SEC" not in captured["env"]

    async def test_engine_secrets_piped_to_stdin_as_json(
        self, spawner: Spawner
    ) -> None:
        """The agent's startup hook reads ``sys.stdin`` once and parses
        a single JSON object. The spawner must write exactly that
        payload and close stdin so ``sys.stdin.read()`` returns cleanly.
        """
        msg = SpawnManifest(
            agent_id="agent-secret",
            engine="claude-code",
            agent_token="tok-xyz",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            engine_secrets={"GEMINI_API_KEY": "sk-abc"},
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        captured_stdin_writes: list[bytes] = []
        close_called = {"flag": False}

        class FakeStdin:
            def write(self, data: bytes) -> None:
                captured_stdin_writes.append(data)

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                close_called["flag"] = True

            async def wait_closed(self) -> None:
                return None

        mock_proc.stdin = FakeStdin()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(msg)

        assert result.success is True
        assert close_called["flag"] is True
        # Exactly one payload, well-formed JSON with only the secrets
        # (and nothing else — no token, no ANYGARDEN_*, etc.)
        assert len(captured_stdin_writes) == 1
        payload = json.loads(captured_stdin_writes[0].decode("utf-8"))
        assert payload == {"GEMINI_API_KEY": "sk-abc"}

    async def test_empty_engine_secrets_still_pipes_empty_object(
        self, spawner: Spawner
    ) -> None:
        """The agent's bootstrap always reads stdin. When there are
        no secrets, the spawner must still write ``{}`` + close so the
        agent's ``read`` returns cleanly instead of blocking on an
        empty pipe.
        """
        msg = SpawnManifest(
            agent_id="agent-secret",
            engine="claude-code",
            agent_token="tok-xyz",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            engine_secrets={},
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = MagicMock()
        mock_proc.stdin.drain = AsyncMock()
        mock_proc.stdin.wait_closed = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(msg)

        write_calls = mock_proc.stdin.write.call_args_list
        assert len(write_calls) == 1
        payload = json.loads(write_calls[0].args[0].decode("utf-8"))
        assert payload == {}
        mock_proc.stdin.close.assert_called_once()


class TestSpawn:
    """Tests for spawning agent subprocesses."""

    async def test_spawn_success(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Should spawn a subprocess and return success with pid."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert result.pid == 42
        assert result.agent_id == "agent-test-001"

    async def test_spawn_uses_agent_root_as_cwd(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """The agent process cwd is the materialized agent root, not
        the legacy ``workspace/`` child.
        """
        captured: dict[str, object] = {}

        async def capture_exec(*args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            return _mock_proc()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured["cwd"] == str(
            spawner._agent_dirs_root / spawn_msg.agent_id
        )
        assert not (
            spawner._agent_dirs_root / spawn_msg.agent_id / "workspace"
        ).exists()

    async def test_spawn_falls_back_to_uvx(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """When anygarden-agent is not in PATH, spawner should use uvx."""
        mock_proc = MagicMock()
        mock_proc.pid = 50
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()
        captured_cmd = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value=None,  # anygarden-agent NOT found
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert "uvx" in captured_cmd[0]

    async def test_spawn_logs_agent_binary_path_source(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """PATH hit path: the spawn must emit
        ``agent_binary_resolved`` with source=path and the discovered
        binary path. This is the forensic trail operators rely on when
        debugging which ``anygarden-agent`` actually ran."""
        mock_proc = MagicMock()
        mock_proc.pid = 60
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ), patch("anygarden_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["source"] == "path"
            assert calls[0].kwargs["path"] == "/usr/local/bin/anygarden-agent"

    async def test_spawn_logs_agent_binary_uvx_source(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """uvx fallback path: same event key, ``source=uvx`` and
        ``path=None`` (the binary is fetched on demand so no stable
        filesystem path exists at spawn time)."""
        mock_proc = MagicMock()
        mock_proc.pid = 61
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value=None,
        ), patch("anygarden_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["source"] == "uvx"
            assert calls[0].kwargs["path"] is None

    async def test_spawn_duplicate_agent_kills_old_and_respawns(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Duplicate spawn should kill the old process and succeed."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = None
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ), patch(
            "anygarden_machine.spawner.terminate_tree"
        ) as mock_terminate:
            first = await spawner.spawn(spawn_msg)
            assert first.success is True
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        # The old process group should have been terminated by PID.
        mock_terminate.assert_called_with(42, timeout=KILL_TIMEOUT)

    async def test_spawn_passes_token_via_env(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Agent token must be passed via ANYGARDEN_TOKEN env var, not argv."""
        captured_env = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            proc = MagicMock()
            proc.pid = 99
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            proc.stdin = AsyncMock()
            return proc

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_env.get("ANYGARDEN_TOKEN") == "secret-token-xyz"

    async def test_spawn_sets_codex_home_when_codex_overlay_present(
        self, spawner: Spawner, tmp_path: Path
    ) -> None:
        """codex 엔진 + ``.codex/*`` 오버레이(MCP 템플릿 또는 admin
        커스텀 config) 조합에서는 ``CODEX_HOME`` 을 per-agent
        ``.codex/`` 로 리다이렉트해야 한다. 빼먹으면 codex app-server
        가 호스트 ``~/.codex/config.toml`` 로 fallback 하고 anygarden 가
        쓴 MCP 오버레이가 silently 무시된다.
        """
        msg = SpawnManifest(
            agent_id="agent-codex",
            engine="codex-cli",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            files={
                ".codex/config.toml": (
                    "[mcp_servers.demo]\ncommand = \"npx\"\nargs = [\"x\"]\n"
                ),
            },
        )

        captured_env: dict[str, str] = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return _mock_proc()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=mock_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            result = await spawner.spawn(msg)

        assert result.success is True
        expected = str(spawner._agent_dirs_root / "agent-codex" / ".codex")
        assert captured_env.get("CODEX_HOME") == expected
        # materialize 의 파일-쓰기 루프가 ``.codex/config.toml`` 을
        # 쓰면서 부모 디렉토리까지 함께 생성한다. CODEX_HOME 이
        # 가리키는 디렉토리에 codex 가 런타임에 auth.json/history
        # 를 쓸 수 있어야 하므로 존재 가드.
        assert Path(expected).is_dir()

    async def test_spawn_does_not_set_codex_home_without_codex_overlay(
        self, spawner: Spawner
    ) -> None:
        """회귀 가드: codex 엔진이라도 ``.codex/*`` 오버레이가
        없으면 ``CODEX_HOME`` 을 건드리지 않아야 한다. 이 경우 codex
        는 호스트 ``~/.codex/config.toml`` + ``~/.codex/auth.json``
        (ChatGPT 로그인) 에 의존하는 정상 스타트업 경로를 타야 한다.
        무조건 리다이렉트는 auth 없는 빈 per-agent ``.codex/`` 로
        codex 를 밀어넣어 인증 실패를 유발한다.
        """
        msg = SpawnManifest(
            agent_id="agent-codex-hostauth",
            engine="codex-cli",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            files={},
        )

        captured_env: dict[str, str] = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return _mock_proc()

        # Strip ambient CODEX_HOME so the test does not false-pass by
        # inheriting a host-level value.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            with patch(
                "anygarden_machine.spawner.asyncio.create_subprocess_exec",
                side_effect=mock_exec,
            ), patch(
                "anygarden_machine.spawner.shutil.which",
                return_value="/usr/local/bin/anygarden-agent",
            ):
                result = await spawner.spawn(msg)

        assert result.success is True
        assert "CODEX_HOME" not in captured_env

    @pytest.mark.parametrize("engine", ["claude-code", "gemini-cli"])
    async def test_spawn_does_not_set_codex_home_for_other_engines(
        self, spawner: Spawner, engine: str
    ) -> None:
        """``CODEX_HOME`` 리다이렉트는 codex 엔진에서만 발생해야 한다.
        다른 엔진에 대해 주입되면 사용자 호스트의 ``~/.codex/`` 를
        우연히 건드리거나, 존재하지 않는 디렉토리를 가리켜 해당 엔진의
        동작에 간섭할 수 있다. 오버레이가 있든 없든 non-codex 엔진
        이면 무조건 스킵.
        """
        msg = SpawnManifest(
            agent_id="agent-other",
            engine=engine,
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            files={".codex/config.toml": "[x]\n"},
        )

        captured_env: dict[str, str] = {}

        async def mock_exec(*args, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            return _mock_proc()

        # Strip ambient CODEX_HOME so the test does not false-pass by
        # inheriting a host-level value.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            with patch(
                "anygarden_machine.spawner.asyncio.create_subprocess_exec",
                side_effect=mock_exec,
            ), patch(
                "anygarden_machine.spawner.shutil.which",
                return_value="/usr/local/bin/anygarden-agent",
            ):
                result = await spawner.spawn(msg)

        assert result.success is True
        assert "CODEX_HOME" not in captured_env

    async def test_spawn_typescript_runtime_uses_anygarden_agent_ts_when_on_path(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — ``runtime='typescript'`` resolves to the
        ``anygarden-agent-ts`` binary when present on PATH. The ``which``
        call must target the TS binary name, not the Python one.
        """
        captured_cmd: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 71
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            proc.stdin = AsyncMock()
            return proc

        def fake_which(name: str):
            if name == "anygarden-agent-ts":
                return "/usr/local/bin/anygarden-agent-ts"
            return None

        spawn_msg.runtime = "typescript"

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch("anygarden_machine.spawner.shutil.which", side_effect=fake_which):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "/usr/local/bin/anygarden-agent-ts"
        # Same --engine/--name/--server contract as the Python arm.
        assert "--engine" in captured_cmd
        assert "--server" in captured_cmd

    async def test_spawn_typescript_runtime_falls_back_to_npx(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — when ``anygarden-agent-ts`` is not installed,
        spawner falls back to ``npx -y @anygarden/agent-ts``. This is the
        "no local install" path on fresh machines."""
        captured_cmd: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 72
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            proc.stdin = AsyncMock()
            return proc

        spawn_msg.runtime = "typescript"

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value=None,  # Nothing installed
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "npx"
        assert captured_cmd[1] == "-y"
        assert captured_cmd[2] == "@anygarden/agent-ts"

    async def test_spawn_typescript_runtime_logs_binary_resolution(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 — the ``agent_binary_resolved`` log line is
        emitted with ``runtime='typescript'`` + source=path|npx so
        operators can tell which runtime and which binary ran."""
        mock_proc = MagicMock()
        mock_proc.pid = 73
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        spawn_msg.runtime = "typescript"

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent-ts",
        ), patch("anygarden_machine.spawner.log") as mock_log:
            await spawner.spawn(spawn_msg)
            calls = [
                c
                for c in mock_log.info.call_args_list
                if c.args and c.args[0] == "agent_binary_resolved"
            ]
            assert len(calls) == 1
            assert calls[0].kwargs["runtime"] == "typescript"
            assert calls[0].kwargs["source"] == "path"
            assert calls[0].kwargs["path"] == "/usr/local/bin/anygarden-agent-ts"

    async def test_spawn_python_runtime_still_default(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Issue #73 regression guard — a manifest with ``runtime``
        unset (or explicitly ``"python"``) must still pick the Python
        binary. No TS binary lookup happens on the default path."""
        captured_cmd: list[str] = []
        which_calls: list[str] = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = MagicMock()
            proc.pid = 74
            proc.wait = AsyncMock(return_value=0)
            proc.stderr = None
            proc.stdin = AsyncMock()
            return proc

        def fake_which(name: str):
            which_calls.append(name)
            if name == "anygarden-agent":
                return "/usr/local/bin/anygarden-agent"
            return None

        # Leave ``spawn_msg.runtime`` at its default.
        assert spawn_msg.runtime == "python"

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            side_effect=capture_exec,
        ), patch("anygarden_machine.spawner.shutil.which", side_effect=fake_which):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        assert captured_cmd[0] == "/usr/local/bin/anygarden-agent"
        # The Python path must not probe for the TS binary.
        assert "anygarden-agent-ts" not in which_calls

    async def test_spawn_profile_chmod(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Profile temp file should be created with chmod 600."""
        chmod_calls = []
        original_chmod = os.chmod

        def track_chmod(path, mode):
            chmod_calls.append((str(path), oct(mode)))
            return original_chmod(path, mode)

        mock_proc = MagicMock()
        mock_proc.pid = 55
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with (
            patch("anygarden_machine.spawner.os.chmod", side_effect=track_chmod),
            patch(
                "anygarden_machine.spawner.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "anygarden_machine.spawner.shutil.which",
                return_value="/usr/local/bin/anygarden-agent",
            ),
        ):
            result = await spawner.spawn(spawn_msg)

        assert result.success is True
        # Check that chmod 600 was called for a anygarden-agent profile file
        chmod_for_profile = [c for c in chmod_calls if "anygarden-agent-" in c[0]]
        assert len(chmod_for_profile) == 1
        assert chmod_for_profile[0][1] == "0o600"


class TestGetRunning:
    """Tests for get_running accessor."""

    async def test_get_running_returns_agent(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """get_running should return the RunningAgent for a spawned agent."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        agent = spawner.get_running("agent-test-001")
        assert agent is not None
        assert agent.agent_id == "agent-test-001"
        assert agent.pid == 42

    def test_get_running_returns_none_for_unknown(self, spawner: Spawner) -> None:
        """get_running should return None for an agent not being tracked."""
        assert spawner.get_running("nonexistent") is None


class TestKill:
    """Tests for killing agent processes."""

    async def test_kill_terminates_tree(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Kill should terminate the agent's full process tree by PID."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = None
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        with patch("anygarden_machine.spawner.terminate_tree") as mock_terminate:
            result = await spawner.kill("agent-test-001")

        assert result["success"] is True
        mock_terminate.assert_called_once_with(42, timeout=KILL_TIMEOUT)

    async def test_kill_skips_already_exited(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        """Should short-circuit when the process has already exited."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0  # already exited
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        with patch("anygarden_machine.spawner.terminate_tree") as mock_terminate:
            result = await spawner.kill("agent-test-001")

        assert result["success"] is True
        assert "already exited" in result.get("note", "")
        mock_terminate.assert_not_called()

    async def test_kill_nonexistent_agent(self, spawner: Spawner) -> None:
        """Should return error for unknown agent_id."""
        result = await spawner.kill("no-such-agent")
        assert result["success"] is False
        assert "not found" in result["error"]


class TestCleanup:
    """Tests for cleanup after agent exit."""

    async def test_cleanup_removes_profile(self, spawner: Spawner, spawn_msg: SpawnManifest) -> None:
        """Cleanup should delete the temp profile file."""
        mock_proc = MagicMock()
        mock_proc.pid = 77
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        # Retrieve actual profile path from the running agent
        agent = spawner._agents[spawn_msg.agent_id]
        profile_path = agent.profile_path
        assert profile_path is not None
        assert profile_path.exists()

        # Cleanup
        spawner._cleanup(spawn_msg.agent_id)

        # Profile should be deleted
        assert not profile_path.exists()
        # Agent should be removed from running list
        assert len(spawner.list_running()) == 0


# ── #451 re-adopt ─────────────────────────────────────────────────────


class TestKillAdoptedAgent:
    """#451 — a re-adopted agent has ``proc=None`` (it's not a child of
    this daemon). ``kill`` must terminate the tree by pid without
    touching ``proc.returncode`` / ``proc.wait``.
    """

    async def test_kill_proc_none_terminates_by_pid(
        self, spawner: Spawner
    ) -> None:
        spawner._agents["adopted-1"] = RunningAgent(
            agent_id="adopted-1",
            pid=4321,
            engine="codex-cli",
            started_at=1_700_000_000.0,
            proc=None,
        )

        with patch("anygarden_machine.spawner.terminate_tree") as mock_terminate:
            result = await spawner.kill("adopted-1")

        assert result["success"] is True
        mock_terminate.assert_called_once_with(4321, timeout=KILL_TIMEOUT)
        # No proc to await; agent removed from tracking.
        assert spawner.get_running("adopted-1") is None

    async def test_kill_proc_none_clears_runtime(
        self, spawner: Spawner
    ) -> None:
        spawner._manifest_store.record_runtime(
            "adopted-1",
            {
                "pid": 4321,
                "pgid": 4321,
                "started_at": 1_700_000_000.0,
                "engine": "codex-cli",
                "generation": 2,
            },
        )
        spawner._agents["adopted-1"] = RunningAgent(
            agent_id="adopted-1",
            pid=4321,
            engine="codex-cli",
            started_at=1_700_000_000.0,
            proc=None,
        )

        with patch("anygarden_machine.spawner.terminate_tree"):
            await spawner.kill("adopted-1")

        assert spawner._manifest_store.load_runtime("adopted-1") is None


class TestAdopt:
    """#451 — ``adopt`` registers a still-live, daemon-orphaned process
    and rejects dead / PID-recycled records.
    """

    def _runtime(self, pid: int = 999, started_at: float = 111.0) -> dict:
        return {
            "pid": pid,
            "pgid": pid,
            "started_at": started_at,
            "engine": "claude-code",
            "generation": 5,
        }

    async def test_adopt_live_group_registers_agent(
        self, spawner: Spawner
    ) -> None:
        runtime = self._runtime(pid=999, started_at=111.0)

        with patch(
            "anygarden_machine.spawner.is_group_alive", return_value=True
        ), patch("anygarden_machine.spawner.psutil.Process") as mock_proc_cls:
            mock_proc_cls.return_value.create_time.return_value = 111.0
            ok = spawner.adopt("agent-adopt", runtime)

        assert ok is True
        agent = spawner.get_running("agent-adopt")
        assert agent is not None
        assert agent.pid == 999
        assert agent.proc is None
        assert agent.engine == "claude-code"
        assert agent.watch_task is not None
        # Cancel the poll watcher so the test loop closes cleanly.
        agent.watch_task.cancel()

    def test_adopt_dead_group_rejected_and_cleared(
        self, spawner: Spawner
    ) -> None:
        spawner._manifest_store.record_runtime(
            "agent-dead", self._runtime(pid=999, started_at=111.0)
        )

        with patch(
            "anygarden_machine.spawner.is_group_alive", return_value=False
        ):
            ok = spawner.adopt("agent-dead", self._runtime(pid=999))

        assert ok is False
        assert spawner.get_running("agent-dead") is None
        # Stale runtime.json must be cleared so it can't mislead next boot.
        assert spawner._manifest_store.load_runtime("agent-dead") is None

    def test_adopt_pid_recycled_rejected(self, spawner: Spawner) -> None:
        """Group is alive but ``create_time`` disagrees with the recorded
        ``started_at`` → the pid was recycled into an unrelated process;
        adoption must be refused.
        """
        spawner._manifest_store.record_runtime(
            "agent-recycled", self._runtime(pid=999, started_at=111.0)
        )

        with patch(
            "anygarden_machine.spawner.is_group_alive", return_value=True
        ), patch("anygarden_machine.spawner.psutil.Process") as mock_proc_cls:
            # Wildly different create_time → recycled pid.
            mock_proc_cls.return_value.create_time.return_value = 999_999.0
            ok = spawner.adopt(
                "agent-recycled", self._runtime(pid=999, started_at=111.0)
            )

        assert ok is False
        assert spawner.get_running("agent-recycled") is None
        assert spawner._manifest_store.load_runtime("agent-recycled") is None

    def test_adopt_pid_gone_rejected(self, spawner: Spawner) -> None:
        """Group probe passes (e.g. EPERM) but the pid itself is gone →
        psutil raises NoSuchProcess → refuse and clear.
        """
        import psutil

        spawner._manifest_store.record_runtime(
            "agent-gone", self._runtime(pid=999, started_at=111.0)
        )

        with patch(
            "anygarden_machine.spawner.is_group_alive", return_value=True
        ), patch(
            "anygarden_machine.spawner.psutil.Process",
            side_effect=psutil.NoSuchProcess(999),
        ):
            ok = spawner.adopt("agent-gone", self._runtime(pid=999))

        assert ok is False
        assert spawner.get_running("agent-gone") is None
        assert spawner._manifest_store.load_runtime("agent-gone") is None

    async def test_poll_watch_fires_stopped_on_group_death(
        self, spawner: Spawner
    ) -> None:
        """The poll watcher invokes the stopped handler and cleans up once
        the adopted group dies. Patch the poll interval to ~0 so the test
        doesn't wait the production 5s.
        """
        stopped = AsyncMock()

        # First probe (during adopt) alive; subsequent probes dead.
        alive_calls = iter([True, True, False])

        def fake_alive(_pgid: int) -> bool:
            try:
                return next(alive_calls)
            except StopIteration:
                return False

        with patch(
            "anygarden_machine.spawner.is_group_alive", side_effect=fake_alive
        ), patch("anygarden_machine.spawner.psutil.Process") as mock_proc_cls, patch(
            "anygarden_machine.spawner.ADOPT_POLL_INTERVAL", 0.01
        ):
            mock_proc_cls.return_value.create_time.return_value = 111.0
            ok = spawner.adopt(
                "agent-poll",
                self._runtime(pid=999, started_at=111.0),
                handle_stopped=stopped,
            )
            assert ok is True
            agent = spawner.get_running("agent-poll")
            assert agent is not None and agent.watch_task is not None
            await agent.watch_task

        stopped.assert_awaited_once_with("agent-poll", 0)


class TestSpawnRecordsRuntime:
    """#451 — a successful spawn persists runtime.json so a restarted
    daemon can re-adopt the process.
    """

    async def test_spawn_records_runtime_json(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        spawn_msg.generation = 9

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        runtime = spawner._manifest_store.load_runtime(spawn_msg.agent_id)
        assert runtime is not None
        assert runtime["pid"] == 42
        assert runtime["pgid"] == 42  # start_new_session=True → pgid==pid
        assert runtime["engine"] == spawn_msg.engine
        assert runtime["generation"] == 9
        assert isinstance(runtime["started_at"], (int, float))

    async def test_cleanup_clears_runtime_json(
        self, spawner: Spawner, spawn_msg: SpawnManifest
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.stderr = None
        mock_proc.stdin = AsyncMock()

        with patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/usr/local/bin/anygarden-agent",
        ):
            await spawner.spawn(spawn_msg)

        assert spawner._manifest_store.load_runtime(spawn_msg.agent_id) is not None

        spawner._cleanup(spawn_msg.agent_id)

        assert spawner._manifest_store.load_runtime(spawn_msg.agent_id) is None


class TestSessionStorePreservation:
    """#532 — codex 세션 스토어가 respawn(re-materialize)을 넘어 보존되는지."""

    def _codex_msg(self, config: str) -> SpawnManifest:
        return SpawnManifest(
            agent_id="agent-codex-sess",
            engine="codex-cli",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            files={".codex/config.toml": config},
        )

    def test_codex_session_store_survives_rematerialize(
        self, spawner: Spawner
    ) -> None:
        """재-materialize가 codex 세션 스토어(sessions/·*.sqlite)를 보존하고
        managed config.toml은 갱신한다 — #532의 핵심."""
        root = spawner._materialize_agent_dir(self._codex_msg("old = 1\n"))
        assert (root / ".codex" / "config.toml").read_text() == "old = 1\n"

        # codex 런타임이 세션 상태를 남긴 것을 흉내낸다.
        sessions = root / ".codex" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "thread-abc.jsonl").write_text("event\n")
        (root / ".codex" / "state.sqlite").write_text("db-bytes")
        (root / ".codex" / "history.jsonl").write_text("h\n")

        # respawn: 새 config 로 재-materialize.
        spawner._materialize_agent_dir(self._codex_msg("new = 2\n"))

        # 세션 스토어 보존 + config 갱신.
        assert (sessions / "thread-abc.jsonl").read_text() == "event\n"
        assert (root / ".codex" / "state.sqlite").read_text() == "db-bytes"
        assert (root / ".codex" / "history.jsonl").read_text() == "h\n"
        assert (root / ".codex" / "config.toml").read_text() == "new = 2\n"

    def test_non_session_managed_dirs_still_wiped_wholesale(
        self, spawner: Spawner
    ) -> None:
        """회귀 가드: 세션 미보유 관리 dir(.claude/.gemini)은 여전히
        트리째 prune된다 — #532가 .codex에만 적용됨을 확인."""
        msg = SpawnManifest(
            agent_id="agent-cc",
            engine="claude-code",
            agent_token="tok",
            profile_yaml="",
            rooms=["r"],
            server_url="wss://localhost:8000/ws/agent",
            files={},
        )
        root = spawner._materialize_agent_dir(msg)

        stray_gemini = root / ".gemini" / "stray.txt"
        stray_gemini.parent.mkdir(parents=True, exist_ok=True)
        stray_gemini.write_text("junk")
        stray_claude = root / ".claude" / "stray.txt"
        stray_claude.parent.mkdir(parents=True, exist_ok=True)
        stray_claude.write_text("junk")

        spawner._materialize_agent_dir(msg)

        assert not stray_gemini.exists()
        assert not stray_claude.exists()
