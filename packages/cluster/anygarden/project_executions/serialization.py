"""User-facing revision, stop and irreversible-effect projections."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from anygarden.db.models import (
    ExecutionInputRevision,
    ExecutionMutation,
    ExecutionStop,
    Participant,
    ProjectExecution,
    Task,
)
from anygarden.project_executions.authorization import disposition
from anygarden.project_executions.mutations import (
    can_manage_execution,
    mutation_payload,
)
from anygarden.project_executions.stop_service import public_stop, stop_summary

log = logging.getLogger(__name__)


class ExecutionUpdateOut(BaseModel):
    type: Literal["execution.updated"] = "execution.updated"
    execution_id: str
    operating_room_id: str
    input_revision: int
    state_revision: int
    status: str
    stop_summary: dict


async def execution_operation_projection(db, execution: ProjectExecution) -> dict:
    """Public reason/action for a task whose project scope is already checked."""
    from anygarden.project_executions.recovery import public_reason_code

    action = await db.scalar(select(ExecutionMutation.action)
        .where(ExecutionMutation.execution_id == execution.id)
        .order_by(ExecutionMutation.requested_at.desc(), ExecutionMutation.id).limit(1))
    return {"execution_operation_action": action if action in {"revise", "cancel", "deadline", "limit"} else None,
            "execution_error": public_reason_code(execution.error)}


async def execution_summary(db, execution: ProjectExecution, *, access=None) -> dict:
    from anygarden.project_executions.service import (
        _as_dict,
        delegation_total_expression,
    )
    result = _as_dict(execution)
    result["delegation_count"] = await db.scalar(select(delegation_total_expression(execution.id)))
    revision = await db.scalar(select(ExecutionInputRevision).where(
        ExecutionInputRevision.execution_id == execution.id,
        ExecutionInputRevision.revision == execution.input_revision,
    ))
    latest = await db.scalar(select(ExecutionMutation).where(ExecutionMutation.execution_id == execution.id)
        .order_by(ExecutionMutation.requested_at.desc(), ExecutionMutation.id).limit(1))
    result.update(can_manage=can_manage_execution(access, execution) if access is not None else False,
        current_constraints=revision.user_constraints if revision else "",
        current_input_files=[{k: v for k, v in item.items() if k not in {"content", "content_base64"}}
            for item in revision.input_files] if revision else [],
        latest_operation=mutation_payload(latest) if latest else None,
        stop_summary=await stop_summary(db, execution.id))
    return result


async def enrich_execution_detail(db, detail: dict, *, execution: ProjectExecution, access=None) -> dict:
    from anygarden.project_executions.recovery import (
        public_task_error,
        task_recovery_payload,
    )

    result = dict(detail)
    result["execution"] = await execution_summary(db, execution, access=access)
    for name in ("tasks", "results", "input_revisions", "approvals"):
        rows = []
        for source in result.get(name, []):
            row = dict(source)
            revision = row.get("revision") if name == "input_revisions" else row.get("input_revision")
            is_current, state = disposition(execution, revision)
            row.update(is_current=is_current, disposition=state)
            if name == "tasks":
                task = await db.get(Task, row["id"])
                row["recovery"] = await task_recovery_payload(db, task, execution=execution, access=access) if task else None
                row["error"] = public_task_error(row.get("status"), row.get("error"))
            if name == "approvals":
                row["permit_revoked"] = not is_current and row.get("status") in {"pending", "approved", "rejected"}
            rows.append(row)
        result[name] = rows
    result["mutations"] = [mutation_payload(row) for row in await db.scalars(select(ExecutionMutation)
        .where(ExecutionMutation.execution_id == execution.id)
        .order_by(ExecutionMutation.requested_at, ExecutionMutation.id))]
    result["stops"] = [public_stop(row) for row in await db.scalars(select(ExecutionStop)
        .where(ExecutionStop.execution_id == execution.id)
        .order_by(ExecutionStop.requested_at, ExecutionStop.id))]
    effect_fields = ("id", "input_revision", "task_id", "source_task_id", "source_result_id", "source_result_version",
        "source_result_sha256", "artifact_filename", "artifact_sha256", "action_kind", "target_alias", "target_label",
        "target_url", "status", "executed_at", "finished_at", "receipt", "error", "is_current", "disposition")
    latest = result["execution"]["latest_operation"]
    result["external_effects"] = [{**{key: row.get(key) for key in effect_fields},
        "send_started": row.get("executed_at") is not None,
        "started_before_mutation": bool(row.get("executed_at") and latest
            and datetime.fromisoformat(row["executed_at"]) <= datetime.fromisoformat(latest["requested_at"])),
        "reversal_supported": False, "native_engine_egress_controlled": False}
        for row in result.get("approvals", [])]
    return result


async def fanout_execution_update(db, *, manager, execution_id: str) -> None:
    """Best-effort postcommit invalidation to currently authorized ops users."""
    if manager is None:
        return
    try:
        execution = await db.get(ProjectExecution, execution_id, populate_existing=True)
        if execution is None:
            return
        frame = ExecutionUpdateOut(execution_id=execution.id, operating_room_id=execution.operating_room_id,
            input_revision=execution.input_revision, state_revision=execution.state_revision, status=execution.status,
            stop_summary=await stop_summary(db, execution.id))
        users = set(await db.scalars(select(Participant.user_id).where(
            Participant.room_id == execution.operating_room_id,
            Participant.user_id.is_not(None), Participant.role.in_({"member", "admin", "owner", "observer"}),
        )))
        await asyncio.wait_for(manager.push_to_users(users, frame), timeout=2)
    except Exception:  # noqa: BLE001 — committed mutation/receipt is canonical
        log.warning("execution_update_fanout_deferred", extra={"execution_id": execution_id})
