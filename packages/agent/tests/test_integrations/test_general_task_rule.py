"""#806 — the general-room task rule tells an agent when to claim its request."""

import pytest

from anygarden_agent.integrations.codex_cli import general_task_rule, turn_workflow
from anygarden_agent.runtime.execution.pi_self_tools import TOOL_NAMES


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"turn_lease": "lease"},
        # An assigned or resumed general task keeps the rule for its questions.
        {"task_assignment": {"task_id": "t1"}},
    ],
)
def test_rule_is_added_for_general_turns(metadata):
    rule = general_task_rule(metadata)
    assert "claim_current_request" in rule
    assert "request_task_input" in rule


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"operating_lead": True},
        {"task_assignment": {"execution_id": "e1", "role": "worker"}},
    ],
)
def test_rule_is_not_added_for_project_work(metadata):
    assert general_task_rule(metadata) == ""


def test_operating_lead_gets_only_the_operating_workflow():
    workflow = turn_workflow({"operating_lead": True})
    assert "[프로젝트 운영실 작업]" in workflow
    assert "claim_current_request" not in workflow


def test_general_turn_gets_only_the_task_rule():
    workflow = turn_workflow({"turn_lease": "lease"})
    assert "claim_current_request" in workflow
    assert "[프로젝트 운영실 작업]" not in workflow


def test_pi_bridge_exposes_the_general_task_tools():
    assert {"claim_current_request", "request_task_input"} <= TOOL_NAMES
