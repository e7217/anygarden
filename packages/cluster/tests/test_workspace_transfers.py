"""#914 — one-time HTTP transfer tickets between server and machine."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from anygarden.db.models import MachineTransfer
from anygarden.workspaces import transfers

from .test_agents_api import agents_env as _agents_env

transfer_env = _agents_env


@pytest.fixture
def spool(transfer_env, tmp_path):
    path = tmp_path / "spool"
    transfer_env["app"].state.config.transfer_spool_dir = path
    return path


async def _issue(env, direction, max_bytes=100, spool_name=None):
    async with env["factory"]() as db:
        return await transfers.issue(db, machine_id=env["machine"].id, agent_id="agent-1",
                                     direction=direction, path="out/a.bin", max_bytes=max_bytes,
                                     spool_name=spool_name)


@pytest.mark.req("WS-07")
@pytest.mark.asyncio
async def test_machine_uploads_once_into_the_spool(transfer_env, spool):
    env = transfer_env
    ticket = await _issue(env, "from_machine")
    url = f"/api/v1/machine-transfers/{ticket.id}"
    headers = {"Authorization": f"Ticket {ticket.token}"}

    put = await env["client"].put(url, content=b"payload", headers=headers)
    assert put.status_code == 200, put.text
    assert put.json() == {"size": 7, "sha256": hashlib.sha256(b"payload").hexdigest()}
    assert (spool / ticket.spool_name).read_bytes() == b"payload"
    again = await env["client"].put(url, content=b"x", headers=headers)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_ticket_refusals(transfer_env, spool):
    env = transfer_env
    ticket = await _issue(env, "from_machine", max_bytes=4)
    url = f"/api/v1/machine-transfers/{ticket.id}"
    assert (await env["client"].put(url, content=b"x")).status_code == 404
    assert (await env["client"].put(url, content=b"x", headers={"Authorization": "Ticket wrong"})).status_code == 404
    bearer = await env["client"].put(url, content=b"x", headers={"Authorization": f"Bearer {env['token']}"})
    assert bearer.status_code == 404
    assert (await env["client"].get(url, headers={"Authorization": f"Ticket {ticket.token}"})).status_code == 405

    big = await env["client"].put(url, content=b"12345", headers={"Authorization": f"Ticket {ticket.token}"})
    assert big.status_code == 413
    assert not (spool / ticket.spool_name).exists()

    expired = await _issue(env, "from_machine")
    async with env["factory"]() as db:
        row = await db.get(MachineTransfer, expired.id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    gone = await env["client"].put(f"/api/v1/machine-transfers/{expired.id}", content=b"x",
                                   headers={"Authorization": f"Ticket {expired.token}"})
    assert gone.status_code == 410


@pytest.mark.req("WS-07")
@pytest.mark.asyncio
async def test_machine_downloads_a_spooled_upload(transfer_env, spool):
    env = transfer_env
    spool.mkdir()
    (spool / "upload-1").write_bytes(b"browser bytes")
    ticket = await _issue(env, "to_machine", spool_name="upload-1")
    url = f"/api/v1/machine-transfers/{ticket.id}"
    got = await env["client"].get(url, headers={"Authorization": f"Ticket {ticket.token}"})
    assert got.status_code == 200 and got.content == b"browser bytes"
    assert (await env["client"].get(url, headers={"Authorization": f"Ticket {ticket.token}"})).status_code == 409


@pytest.mark.asyncio
async def test_cleanup_removes_old_tickets_and_orphan_spool_files(transfer_env, spool):
    env = transfer_env
    ticket = await _issue(env, "from_machine")
    spool.mkdir()
    (spool / ticket.spool_name).write_bytes(b"x")
    (spool / "orphan").write_bytes(b"y")
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    import os
    old = (later - timedelta(hours=2)).timestamp()
    os.utime(spool / "orphan", (old, old))
    async with env["factory"]() as db:
        removed = await transfers.cleanup(db, spool, now=later)
        assert removed == 1
        assert await db.get(MachineTransfer, ticket.id) is None
    assert list(spool.iterdir()) == []

