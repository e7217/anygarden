"""The cluster's ``RemoteScope`` mirrors the agent runtime's ``SessionScope``.

A remote agent's prepare response carries its full ``SessionScope``; the
cluster rebuilds it as ``RemoteScope`` and both sides compare ``key``.
When a field was added only on the agent side, every federated prepare
failed with a swallowed TypeError (#778).
"""

from __future__ import annotations

from dataclasses import asdict, fields

import pytest
from anygarden.federation.remote_execution import RemoteScope
from anygarden_agent.runtime.execution.contracts import SessionScope

BASE = dict(
    execution_node_id="node-b",
    agent_id="agent-1",
    authority_node_id="node-a",
    channel_id="channel-1",
    thread_root_id=None,
    workspace_binding_id="binding-1",
    workspace_epoch=1,
    policy_epoch=2,
)


def test_fields_match_agent_session_scope():
    assert [f.name for f in fields(RemoteScope)] == [f.name for f in fields(SessionScope)]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"project_execution_id": "7d4f2a0e-0000-4000-8000-000000000001", "input_revision": 3},
        {"retry_request_id": "7d4f2a0e-0000-4000-8000-000000000002", "retry_attempt": 1},
    ],
)
def test_key_matches_agent_session_scope(extra):
    agent = SessionScope(**BASE, **extra)

    remote = RemoteScope(**asdict(agent))

    assert remote.key == agent.key
