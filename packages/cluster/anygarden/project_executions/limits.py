"""Close automatic execution limits without starting another model turn."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, update

from anygarden.db.models import (
    AgentTurn,
    AgentTurnAttempt,
    ExecutionMutation,
    Message,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskResult,
)
from anygarden.messages.service import append_message, fanout_task_event
from anygarden.project_executions.authorization import ACTIVE_STATES
from anygarden.project_executions.service import _event, _queue_message
from anygarden.project_executions.stop_service import (
    fence_execution_turns,
    settle_execution_mutation,
    stop_summary,
)

DEADLINE_REASON = "EXECUTION_DEADLINE_REACHED"
USAGE_LIMIT_REASONS = frozenset({
    "EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
})


def _usage_report(execution) -> str:
    summary = execution.usage_summary
    if not isinstance(summary, dict):
        return "모델 사용량·비용의 실행별 집계는 미수집입니다. 0으로 계산하지 않습니다."
    limits = execution.limits or {}
    calls = summary.get("native_invocations_reserved")
    tokens = summary.get("known_total_tokens")
    cost = summary.get("known_cost_usd")
    rows = [
        f"- 실행 호출 허가: {calls if calls is not None else '미수집'}"
        + (f" / 한도 {limits['max_native_invocations']}" if 'max_native_invocations' in limits else ''),
        f"- 측정된 토큰 소계: {tokens if tokens is not None else '미수집'}"
        + (f" / 관찰 한도 {limits['max_total_tokens']}" if 'max_total_tokens' in limits else ''),
        "- 사용량 상태: 측정 " + str(summary.get("measured_invocations", 0))
        + " · 정산 대기 " + str(summary.get("pending_invocations", 0))
        + " · 미확인 " + str(summary.get("unknown_invocations", 0)),
        f"- 측정된 비용 소계: {'미수집' if cost is None else '$' + str(cost)}"
        + f" · 비용 미수집 호출 {summary.get('cost_unknown_invocations', 0)}",
        f"- 집계 시점: {summary.get('as_of') or '미수집'}",
    ]
    return ("\n".join(rows)
        + "\n하위 작업·재시도·이전 입력 버전의 호출을 함께 집계합니다. "
        "대기·미확인 사용량과 미수집 비용은 소계에 포함하거나 0으로 계산하지 않습니다. "
        "토큰은 종료 후 확인한 사용량이며, 이미 진행 중인 호출의 추가 사용량은 늦게 정산될 수 있습니다.")


async def fanout_deadline_tasks(db, *, manager, task_ids) -> None:
    if manager is None:
        return
    for task_id in task_ids:
        task = await db.get(Task, task_id)
        if task is not None:
            room = await db.get(Room, task.room_id)
            await fanout_task_event(db, manager=manager, event="updated", task=task,
                                   room_name=room.name if room else "")


async def _deadline_report(db, execution, mutation, tasks) -> Message:
    automatic_usage_limit = mutation.action == "limit"
    reason_code = mutation.reason
    heading = "실행 사용량 한도 도달" if automatic_usage_limit else "실행 기한 도달"
    boundary = (f"설정 한도: {json.dumps(execution.limits, ensure_ascii=False, sort_keys=True)}"
                if automatic_usage_limit else f"기한: {execution.deadline_at.isoformat()}")
    completed = []
    unfinished = []
    for task in tasks:
        if task.status == "done":
            result = await db.scalar(select(TaskResult).where(
                TaskResult.task_id == task.id,
                TaskResult.execution_id == execution.id,
                TaskResult.input_revision == execution.input_revision,
                TaskResult.version == task.result_version,
            ))
            if result is not None:
                completed.append(f"- {task.title}: 결과 v{result.version} · 작업 {task.id}")
                seen = set()
                for artifact in result.artifacts or []:
                    if (artifact.get("room_id") == execution.operating_room_id
                        and artifact.get("url") and artifact.get("artifact_id") not in seen):
                        seen.add(artifact["artifact_id"])
                        completed.append(f"  [{artifact.get('filename', '산출물')}]({artifact['url']})")
                continue
        unfinished.append(f"- {task.title}: {task.status} · 작업 {task.id}")
    stops = await stop_summary(db, execution.id)
    process_note = (
        "이 실행에 중단 확인을 기다리는 프로세스는 없습니다. 실행은 실패로 종료됐습니다."
        if stops["all_confirmed"]
        else "진행 중 프로세스의 정확한 중단 확인을 기다립니다. 중단 확인 불명은 완료로 처리하지 않습니다."
    )
    original = await db.get(Message, execution.source_message_id)
    content = (
        f"{heading} · {execution.objective}\n\n"
        f"{boundary}\n원인: {reason_code}\n"
        f"보고 시점: {datetime.now(UTC).isoformat()}\n"
        "새 위임·재시도·모델 시작을 차단했습니다. 설정된 한도의 자동 처리입니다.\n"
        f"보고 시점의 중단 확인: {process_note}\n"
        "후속 중단 확인과 현재 상태는 실행 상세에서 확인할 수 있습니다.\n\n완료된 단계\n"
        + ("\n".join(completed) or "- 승인된 완료 결과 없음")
        + "\n\n미완료 단계\n"
        + ("\n".join(unfinished) or "- 없음")
        + "\n\n실행별 사용량\n" + _usage_report(execution) + "\n\n"
        + "이미 기록된 산출물과 외부 효과는 보존하며 외부 효과를 철회했다고 보고하지 않습니다. "
        "원인과 남은 작업을 확인한 뒤 별도 실행을 준비하세요."
    )
    message = await append_message(
        db, execution.operating_room_id, None, content,
        {"ingest_only": True, "system_origin": "execution_usage_limit" if automatic_usage_limit else "execution_deadline",
         "execution_id": execution.id, "input_revision": execution.input_revision,
         "mutation_id": mutation.id, "reason_code": reason_code},
        thread_root_id=(original.root_message_id or original.id) if original else None,
    )
    mutation.source_message_id = message.id
    _queue_message(db, message)
    return message


async def expire_execution_deadlines(db, *, now=None, limit=100) -> list[str]:
    """Fence expired runs transactionally; exact stop receipts settle failure."""
    now = now or datetime.now(UTC)
    rows = list(await db.scalars(select(ProjectExecution)
        .join(Room, Room.id == ProjectExecution.operating_room_id)
        .where(ProjectExecution.status.in_(ACTIVE_STATES),
               ProjectExecution.deadline_at.is_not(None),
               ProjectExecution.deadline_at <= now,
               Room.project_id == ProjectExecution.project_id,
               Room.archived_at.is_(None))
        .order_by(ProjectExecution.deadline_at, ProjectExecution.id).limit(limit)))
    expired = []
    for execution in rows:
        changed = await db.scalar(update(ProjectExecution).where(
            ProjectExecution.id == execution.id,
            ProjectExecution.input_revision == execution.input_revision,
            ProjectExecution.state_revision == execution.state_revision,
            ProjectExecution.status.in_(ACTIVE_STATES),
            ProjectExecution.deadline_at <= now,
        ).values(status="cancelling", error=DEADLINE_REASON,
                 state_revision=ProjectExecution.state_revision + 1,
                 updated_at=now).returning(ProjectExecution.id))
        if changed is None:
            continue
        await db.refresh(execution)
        identity = {"execution_id": execution.id, "input_revision": execution.input_revision,
                    "deadline_at": execution.deadline_at.isoformat()}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        mutation = ExecutionMutation(
            execution_id=execution.id,
            operation_id=str(uuid5(NAMESPACE_URL, "anygarden:deadline:" + encoded)),
            request_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
            action="deadline", previous_input_revision=execution.input_revision,
            input_revision=execution.input_revision,
            expected_state_revision=execution.state_revision - 1,
            requested_by_user_id=None, root_task_id=execution.root_task_id,
            reason=DEADLINE_REASON, requested_at=now,
        )
        db.add(mutation)
        await db.flush()
        tasks = list(await db.scalars(select(Task).where(
            Task.execution_id == execution.id,
            Task.input_revision == execution.input_revision,
        ).order_by(Task.created_at, Task.id)))
        previous = [{"task_id": task.id, "status": task.status,
                     "result_version": task.result_version}
                    for task in tasks]
        for task in tasks:
            if task.status in {"todo", "in_progress"}:
                task.status = "blocked"
                task.error = DEADLINE_REASON
                db.info.setdefault("project_execution_deadline_tasks", set()).add(task.id)
        await db.flush()
        await _event(db, execution, f"mutation:{mutation.id}:requested",
                     "execution_deadline_reached", task_id=execution.root_task_id,
                     details={"mutation_id": mutation.id, **identity,
                              "reason_code": DEADLINE_REASON, "previous_tasks": previous})
        await fence_execution_turns(db, execution=execution, mutation=mutation)
        await settle_execution_mutation(db, execution.id)
        await _deadline_report(db, execution, mutation, tasks)
        await db.flush()
        expired.append(execution.id)
    return expired


async def close_execution_limit(db, *, execution_id: str | None,
                                reason_code: str) -> bool:
    """Atomically fence one run and request exact stops; caller owns commit.

    Only closed accounting reasons can enter here. Final usage arriving after
    completion updates accounting without turning completed work into failure.
    No extra model invocation is created to produce this partial report.
    """
    if execution_id is None or reason_code not in USAGE_LIMIT_REASONS:
        return False
    execution = await db.get(ProjectExecution, execution_id, populate_existing=True,
                             with_for_update=True)
    if execution is None or execution.status not in ACTIVE_STATES:
        return False
    if reason_code == "EXECUTION_NATIVE_INVOCATION_LIMIT":
        from anygarden.db.native_invocation_models import NativeInvocationAccounting

        pending = list(await db.scalars(select(NativeInvocationAccounting)
            .join(AgentTurnAttempt, AgentTurnAttempt.id == NativeInvocationAccounting.attempt_id)
            .join(AgentTurn, AgentTurn.request_id == NativeInvocationAccounting.request_id)
            .where(NativeInvocationAccounting.execution_id == execution.id,
                   AgentTurnAttempt.state.in_({"leased", "started", "completing"}),
                   AgentTurn.state.in_({"pending", "leased", "retrying", "completing"}))))
        if pending:
            # The admission counter already rejects N+1. Keep N's current
            # workflow authority while it can still publish/finalize. Persist
            # the denied intent so the recovery tick can close unfinished work
            # after the permitted turn reaches a terminal boundary. Native
            # engine_call_finished precedes the SDK completion reply; measured
            # usage or process exit alone cannot revoke that reply's authority.
            key = f"execution:{execution.id}:native-admission-denied"
            existing = await db.scalar(select(ProjectExecutionEvent.id).where(
                ProjectExecutionEvent.event_key == key))
            if existing is None:
                original = await db.get(Message, execution.source_message_id)
                message = await append_message(db, execution.operating_room_id, None,
                    f"추가 실행 한도 도달 · {execution.objective}\n\n"
                    f"원인: {reason_code}\n설정 한도: {json.dumps(execution.limits, ensure_ascii=False)}\n"
                    "새 위임·재시도·모델 호출은 차단했습니다. 이미 허용된 호출은 결과를 게시하고 "
                    "마무리할 수 있으며, 현재 실행을 완료 또는 중단 확인으로 보고하지 않습니다.\n"
                    f"관찰된 사용량:\n{_usage_report(execution)}\n"
                    "미수집 사용량·비용은 0이 아닙니다. 현재 허용된 호출 종료 후 남은 작업과 "
                    "중단 확인은 실행 상세에서 확인하세요.",
                    {"ingest_only": True, "system_origin": "execution_native_admission_limit",
                     "execution_id": execution.id, "input_revision": execution.input_revision,
                     "reason_code": reason_code},
                    thread_root_id=(original.root_message_id or original.id) if original else None)
                event = await _event(db, execution, key, "execution_native_admission_denied",
                    task_id=execution.root_task_id,
                    details={"reason_code": reason_code, "limits": execution.limits,
                             "usage_summary": execution.usage_summary,
                             "permitted_invocations_finishing": True})
                event.message_id = message.id
                _queue_message(db, message)
            await db.flush()
            return False
    now = datetime.now(UTC)
    changed = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.input_revision == execution.input_revision,
        ProjectExecution.state_revision == execution.state_revision,
        ProjectExecution.status.in_(ACTIVE_STATES),
    ).values(status="cancelling", error=reason_code,
             state_revision=ProjectExecution.state_revision + 1,
             updated_at=now).returning(ProjectExecution.id))
    if changed is None:
        return False
    await db.refresh(execution)
    identity = {"execution_id": execution.id, "reason_code": reason_code}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    mutation = ExecutionMutation(
        execution_id=execution.id,
        operation_id=str(uuid5(NAMESPACE_URL, "anygarden:usage-limit:" + encoded)),
        request_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        action="limit", previous_input_revision=execution.input_revision,
        input_revision=execution.input_revision,
        expected_state_revision=execution.state_revision - 1,
        requested_by_user_id=None, root_task_id=execution.root_task_id,
        reason=reason_code, requested_at=now,
    )
    db.add(mutation)
    await db.flush()
    tasks = list(await db.scalars(select(Task).where(
        Task.execution_id == execution.id,
        Task.input_revision == execution.input_revision,
    ).order_by(Task.created_at, Task.id)))
    previous = [{"task_id": task.id, "status": task.status,
                 "result_version": task.result_version} for task in tasks]
    for task in tasks:
        if task.status in {"todo", "in_progress"}:
            task.status = "blocked"
            task.error = reason_code
            db.info.setdefault("project_execution_deadline_tasks", set()).add(task.id)
    await db.flush()
    await _event(db, execution, f"mutation:{mutation.id}:requested",
                 "execution_usage_limit_reached", task_id=execution.root_task_id,
                 details={"mutation_id": mutation.id, **identity,
                          "input_revision": execution.input_revision,
                          "limits": execution.limits,
                          "usage_summary": execution.usage_summary,
                          "previous_tasks": previous})
    await fence_execution_turns(db, execution=execution, mutation=mutation)
    await settle_execution_mutation(db, execution.id)
    await _deadline_report(db, execution, mutation, tasks)
    await db.flush()
    db.info.setdefault("project_execution_usage_updated", set()).add(execution.id)
    return True


async def apply_queued_usage_denials(app, *, denials: dict) -> list[str]:
    """Revalidate denied intent after its transaction rolled back or committed.

    This separate transaction cannot be nested inside an open writer. Callers
    capture the closed identity/reason queue, release their transaction first,
    then invoke this function. A stale denial can never close another run.
    """
    if not denials:
        return []
    from anygarden.mcp.project_tools import broadcast_project_messages
    from anygarden.project_executions.serialization import fanout_execution_update
    from anygarden.project_executions.stop_service import deliver_pending_stops
    from anygarden.project_executions.usage import admission_disposition

    factory = app.state.session_factory
    manager = getattr(app.state, "connection_manager", None)
    closed = []
    async with factory() as db:
        for execution_id, reason_code in sorted(denials.items()):
            if reason_code not in USAGE_LIMIT_REASONS:
                continue
            execution = await db.get(ProjectExecution, execution_id,
                                     populate_existing=True, with_for_update=True)
            if execution is None or execution.status not in ACTIVE_STATES:
                continue
            admission = await admission_disposition(db, execution=execution, phase="intent")
            if (not admission.allowed and admission.reason_code == reason_code
                and await close_execution_limit(db, execution_id=execution_id,
                                                reason_code=reason_code)):
                closed.append(execution_id)
        messages = list(db.info.pop("project_execution_messages", []))
        task_ids = set(db.info.pop("project_execution_deadline_tasks", []))
        await db.commit()
        await broadcast_project_messages(db, app=app, messages=messages)
        await fanout_deadline_tasks(db, manager=manager, task_ids=task_ids)
        for execution_id in closed:
            await fanout_execution_update(db, manager=manager, execution_id=execution_id)
    if manager is not None:
        await deliver_pending_stops(factory, manager)
    return closed


async def reconcile_native_limit_denials(db, *, limit: int = 100) -> list[str]:
    """Finish durable admission denials after N can no longer publish work."""
    execution_ids = list(await db.scalars(select(ProjectExecution.id)
        .join(ProjectExecutionEvent, ProjectExecutionEvent.execution_id == ProjectExecution.id)
        .where(ProjectExecution.status.in_(ACTIVE_STATES),
               ProjectExecutionEvent.event_type == "execution_native_admission_denied")
        .order_by(ProjectExecution.created_at, ProjectExecution.id).limit(limit)))
    closed = []
    from anygarden.project_executions.usage import admission_disposition

    for execution_id in execution_ids:
        execution = await db.get(ProjectExecution, execution_id, populate_existing=True)
        admission = await admission_disposition(db, execution=execution, phase="intent")
        if (not admission.allowed and admission.reason_code == "EXECUTION_NATIVE_INVOCATION_LIMIT"
            and await close_execution_limit(db, execution_id=execution_id,
                                            reason_code=admission.reason_code)):
            closed.append(execution_id)
    return closed
