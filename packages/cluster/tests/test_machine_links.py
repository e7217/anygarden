"""A node serving another server as one of its machines (#848)."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import pytest

from anygarden.machine.agent_root_lock import AgentRootLock
from anygarden.machine.link_code import encode_link_code
from anygarden.node.machine_links import MachineLinkError, MachineLinkManager

TOKEN = "mch_" + "c" * 43


class FakeDaemon:
    instances: list[FakeDaemon] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.connection_state = "connected"
        self.last_error = None
        self.drained = False
        self._spawner = type("Spawner", (), {"list_running": lambda _self: [{}, {}]})()
        FakeDaemon.instances.append(self)

    async def run(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.drained = True
            return


@pytest.fixture(autouse=True)
def _reset():
    FakeDaemon.instances.clear()


def _manager(tmp_path: Path, **kwargs) -> MachineLinkManager:
    return MachineLinkManager(
        tmp_path,
        local_machine_id="own-machine",
        agent_environment={"HOME": "/home/test"},
        daemon_factory=FakeDaemon,
        **kwargs,
    )


def _code(machine_id: str = "m1", server: str = "http://x.test:8000") -> str:
    return encode_link_code(server, machine_id, TOKEN, "Y remote")


@pytest.mark.req("NODE-12")
async def test_add_saves_privately_and_runs_an_isolated_daemon(tmp_path):
    manager = _manager(tmp_path)
    described = await manager.add(_code())
    assert described == {
        "machine_id": "m1",
        "name": "Y remote",
        "server": "http://x.test:8000",
        "status": "connected",
        "error": None,
        "running_agents": 2,
    }
    link_dir = tmp_path / "remote-machines" / "m1"
    assert stat.S_IMODE((link_dir / "machine.token").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "remote-machines").stat().st_mode) == 0o700
    kwargs = FakeDaemon.instances[0].kwargs
    assert kwargs["server_url"] == "ws://x.test:8000/ws/machines/m1"
    assert kwargs["machine_token"] == TOKEN
    assert kwargs["agent_dirs_root"] == link_dir / "agents"
    assert kwargs["token_path"] == link_dir / "machine.token"
    assert kwargs["workspace_signing_key_path"] == link_dir / "workspace-signing.key"
    assert kwargs["allow_self_update"] is False
    assert kwargs["agent_environment"] == {"HOME": "/home/test"}
    # The link holds its own directory, not the node's agents/.
    with pytest.raises(Exception):
        AgentRootLock(link_dir / "agents").acquire()
    other = AgentRootLock(tmp_path / "agents")
    other.acquire()
    other.release()
    await manager.close()


async def test_own_machine_and_duplicates_are_refused(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(MachineLinkError) as own:
        await manager.add(_code("own-machine"))
    assert own.value.status == 409
    await manager.add(_code())
    with pytest.raises(MachineLinkError) as dup:
        await manager.add(_code())
    assert dup.value.code == "MACHINE_LINK_EXISTS"
    with pytest.raises(MachineLinkError) as bad:
        await manager.add("aginvite1.nope")
    assert bad.value.code == "MACHINE_LINK_CODE_INVALID"
    await manager.close()


@pytest.mark.req("NODE-12")
async def test_remove_stops_agents_and_forgets_the_connection(tmp_path):
    manager = _manager(tmp_path)
    await manager.add(_code())
    await asyncio.sleep(0)
    daemon = FakeDaemon.instances[0]
    await manager.remove("m1")
    assert daemon.drained
    assert manager.list() == []
    assert not (tmp_path / "remote-machines" / "m1").exists()
    with pytest.raises(MachineLinkError) as missing:
        await manager.remove("m1")
    assert missing.value.status == 404


@pytest.mark.req("NODE-12")
async def test_links_resume_with_the_node(tmp_path):
    first = _manager(tmp_path)
    await first.add(_code("m1"))
    await first.add(_code("m2", "https://z.test"))
    await asyncio.sleep(0)
    await first.close()
    assert all(daemon.drained for daemon in FakeDaemon.instances)

    FakeDaemon.instances.clear()
    second = _manager(tmp_path)
    await second.start_all()
    assert sorted(link["machine_id"] for link in second.list()) == ["m1", "m2"]
    assert {d.kwargs["machine_token"] for d in FakeDaemon.instances} == {TOKEN}
    await second.close()


@pytest.mark.req("NODE-12")
async def test_a_directory_in_use_is_reported_not_fatal(tmp_path):
    first = _manager(tmp_path)
    await first.add(_code())
    await first.close()
    held = AgentRootLock(tmp_path / "remote-machines" / "m1" / "agents")
    held.acquire()
    try:
        second = _manager(tmp_path)
        await second.start_all()
        [link] = second.list()
        assert link["status"] == "error"
        assert "already uses" in link["error"]
        with pytest.raises(MachineLinkError) as busy:
            await second.remove("m1")
        assert busy.value.status == 409
        assert (tmp_path / "remote-machines" / "m1" / "machine.token").exists()
        await second.close()
    finally:
        held.release()


async def test_start_all_without_links_is_a_no_op(tmp_path):
    manager = _manager(tmp_path)
    await manager.start_all()
    assert manager.list() == []
    await manager.close()


# ── admin API on an integrated node ───────────────────────────────────


@pytest.fixture
def node_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anygarden.app import create_app
    from anygarden.config import AnygardenSettings

    for key in (
        "SKILL_STALE_INTERVAL_HOURS",
        "ORPHAN_SWEEPER_INTERVAL_SEC",
        "TURN_RECOVERY_INTERVAL_SEC",
    ):
        monkeypatch.setenv("ANYGARDEN_" + key, "0")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr("anygarden.node.machine_links.MachineDaemon", FakeDaemon)
    from unittest.mock import AsyncMock

    from anygarden.machine.detector import DetectionResult

    monkeypatch.setattr(
        "anygarden.machine.daemon.detect_engines",
        AsyncMock(return_value=DetectionResult(engines=[])),
    )
    config = AnygardenSettings(
        local_node_data_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'node.db'}",
        room_files_dir=tmp_path / "files",
        artifact_files_dir=tmp_path / "artifacts",
    )
    with TestClient(create_app(config)) as client:
        admin = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.test", "password": "test-password-123"},
        ).json()
        member = client.post(
            "/api/v1/auth/register",
            json={"email": "member@example.test", "password": "test-password-123"},
        ).json()
        client.admin = {"Authorization": "Bearer " + admin["token"]}
        client.member = {"Authorization": "Bearer " + member["token"]}
        yield client


@pytest.mark.req("NODE-13")
def test_admin_adds_lists_and_removes_a_link_without_seeing_the_token(node_client):
    client = node_client
    url = "/api/v1/machine-links"
    assert client.get(url, headers=client.member).status_code == 403
    assert client.post(url, headers=client.member, json={"code": _code()}).status_code == 403

    bad = client.post(url, headers=client.admin, json={"code": "aginvite1.x"})
    assert bad.status_code == 400
    garbled = client.post(url, headers=client.admin, content=b"{nope " + TOKEN.encode())
    assert garbled.status_code == 422
    assert TOKEN not in garbled.text

    created = client.post(url, headers=client.admin, json={"code": _code()})
    assert created.status_code == 201, created.text
    assert created.json()["machine_id"] == "m1"
    assert TOKEN not in created.text
    listed = client.get(url, headers=client.admin)
    assert [link["machine_id"] for link in listed.json()] == ["m1"]
    assert TOKEN not in listed.text
    assert client.post(url, headers=client.admin, json={"code": _code()}).status_code == 409

    own = client.app.state.local_machine_id
    assert client.post(url, headers=client.admin, json={"code": _code(own)}).status_code == 409

    assert client.delete(f"{url}/m1", headers=client.admin).status_code == 204
    assert client.get(url, headers=client.admin).json() == []
    assert client.delete(f"{url}/m1", headers=client.admin).status_code == 404


@pytest.mark.req("NODE-13")
def test_links_need_an_integrated_node(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from anygarden.app import create_app
    from anygarden.config import AnygardenSettings

    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    config = AnygardenSettings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")
    with TestClient(create_app(config)) as client:
        token = client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.test", "password": "test-password-123"},
        ).json()["token"]
        response = client.get(
            "/api/v1/machine-links", headers={"Authorization": "Bearer " + token}
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MACHINE_LINKS_UNAVAILABLE"
