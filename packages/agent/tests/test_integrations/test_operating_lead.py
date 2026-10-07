"""#802 — the operating lead is signalled by ``operating_lead`` on a leased
delivery, not by a room speaker strategy."""

import pytest

from anygarden_agent.integrations.codex_cli import operating_lead_workflow
from anygarden_agent.integrations.room_execution import RoomExecutionAdapter


def test_workflow_is_added_for_the_operating_lead():
    assert "[프로젝트 운영실 작업]" in operating_lead_workflow({"operating_lead": True})


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"operating_lead": False},
        # A delegated execution task runs as a worker, not as the lead.
        {
            "operating_lead": True,
            "task_assignment": {"execution_id": "e1", "role": "worker"},
        },
    ],
)
def test_workflow_is_not_added_otherwise(metadata):
    assert operating_lead_workflow(metadata) == ""


def test_lead_orchestration_task_keeps_the_workflow():
    metadata = {
        "operating_lead": True,
        "task_assignment": {"execution_id": "e1", "role": "orchestration"},
    }
    assert operating_lead_workflow(metadata)


def test_operating_lead_source_turn_gets_an_execution_source_scope():
    adapter = RoomExecutionAdapter()
    msg = {
        "room_id": "room",
        "metadata": {"turn_lease": "lease", "request_id": "r1", "operating_lead": True},
    }
    assert adapter._project_session_scope(msg) == ("execution-source-v1", "r1")


def test_other_leased_turn_has_no_execution_source_scope():
    adapter = RoomExecutionAdapter()
    msg = {"room_id": "room", "metadata": {"turn_lease": "lease", "request_id": "r1"}}
    assert adapter._project_session_scope(msg) is None
