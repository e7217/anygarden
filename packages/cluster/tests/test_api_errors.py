"""Snapshots for the additive v1 machine/task public error contract."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from anygarden.api.v1.errors import (
    PUBLIC_ERROR_CODES,
    make_api_error,
    public_api_error_handler,
)
from starlette.requests import Request

STRING_DETAIL_CASES = (
    (
        "MACHINE_REGISTRATION_FORBIDDEN",
        403,
        "Only users can register machines",
        {},
    ),
    ("MACHINE_LIST_FORBIDDEN", 403, "Forbidden", {}),
    ("MACHINE_OFFLINE", 409, "Machine is not connected", {}),
    ("MACHINE_NOT_FOUND", 404, "Machine not found", {}),
    ("MACHINE_ACCESS_DENIED", 403, "Not the owner of this machine", {}),
    (
        "TASK_ASSIGNEE_NOT_IN_ROOM",
        400,
        "assignee_participant_id is not a participant of this room",
        {"field": "assignee_participant_id"},
    ),
    ("TASK_ROOM_NOT_FOUND", 404, "Room not found", {}),
    ("TASK_SOURCE_MESSAGE_NOT_FOUND", 404, "Message not found", {}),
    ("TASK_NOT_FOUND", 404, "Task not found", {}),
    (
        "TASK_INVALID_MUTATION",
        400,
        "Change an assignee or a status in one request, not both",
        {},
    ),
    (
        "TASK_ROOM_PARTICIPANT_REQUIRED",
        403,
        "Room participant required",
        {},
    ),
    (
        "TASK_HUMAN_ASSIGNMENT_DISABLED",
        403,
        "Human task assignment is disabled for this room",
        {},
    ),
)


@pytest.mark.parametrize(
    ("code", "status_code", "message", "context"),
    STRING_DETAIL_CASES,
    ids=[case[0] for case in STRING_DETAIL_CASES],
)
@pytest.mark.asyncio
async def test_string_detail_error_snapshots(
    code: str,
    status_code: int,
    message: str,
    context: dict[str, Any],
) -> None:
    """Every migrated string detail stays unchanged; metadata is additive."""
    exc = make_api_error(
        code=code,
        status_code=status_code,
        message=message,
        **context,
    )

    response = await public_api_error_handler(
        Request({"type": "http", "method": "GET", "path": "/"}), exc
    )

    assert response.status_code == status_code
    assert json.loads(response.body) == {
        "detail": message,
        "code": code,
        "message": message,
        **context,
    }


@pytest.mark.asyncio
async def test_machine_active_agents_object_detail_snapshot() -> None:
    """The sole pre-existing object detail keeps all legacy nested fields."""
    message = (
        "2 agent(s) are still placed on this machine. Stop or reassign them, "
        "or pass ?force=true to forcibly stop them."
    )
    exc = make_api_error(
        code="MACHINE_HAS_ACTIVE_AGENTS",
        status_code=409,
        message=message,
        legacy_error="machine_has_active_agents",
        agent_count=2,
    )

    response = await public_api_error_handler(
        Request({"type": "http", "method": "DELETE", "path": "/"}), exc
    )

    assert json.loads(response.body) == {
        "detail": {
            "error": "machine_has_active_agents",
            "agent_count": 2,
            "message": message,
        },
        "code": "MACHINE_HAS_ACTIVE_AGENTS",
        "message": message,
    }


def test_machine_task_call_sites_match_public_error_registry() -> None:
    """Adding a migrated error requires an explicit snapshot and registry entry."""
    api_dir = Path(__file__).parents[1] / "anygarden" / "api" / "v1"
    observed: set[str] = set()
    for filename in ("machines.py", "tasks.py"):
        tree = ast.parse((api_dir / filename).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "make_api_error":
                continue
            code_keyword = next(
                (keyword for keyword in node.keywords if keyword.arg == "code"), None
            )
            assert code_keyword is not None
            assert isinstance(code_keyword.value, ast.Constant)
            assert isinstance(code_keyword.value.value, str)
            observed.add(code_keyword.value.value)

    snapshotted = {case[0] for case in STRING_DETAIL_CASES} | {
        "MACHINE_HAS_ACTIVE_AGENTS"
    }
    assert observed == set(PUBLIC_ERROR_CODES) == snapshotted


def test_error_context_cannot_replace_envelope_fields() -> None:
    with pytest.raises(ValueError, match="cannot replace envelope fields: detail"):
        make_api_error(
            code="MACHINE_NOT_FOUND",
            status_code=404,
            message="Machine not found",
            detail="forged",
        )
