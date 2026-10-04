"""Durable bounded repair intents; callers own commit, transport and polling.

No hooks are installed here. A reservation consumes one logical repair
assignment and one real follow-up QA task before native work can start. Old
results, failing QA and managed effects remain immutable historical evidence.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from copy import copy, deepcopy
from hashlib import sha256

from sqlalchemy import exists, func, or_, select, update

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    ExecutionInputRevision,
    Participant,
    ProjectExecution,
    ProjectExecutionEvent,
    Room,
    Task,
    TaskBlocker,
    TaskResult,
)
from anygarden.rooms.authorization import AGENT_EXECUTION_ROLES

REPAIR_EVENT = "qa_repair_assignment"
SAFE_PROCESS_STATES = frozenset({"finished", "stopped", "not_started"})


@asynccontextmanager
async def repair_savepoint(db):
    """Roll back queued postcommit effects together with their SQL savepoint.

    Message ORM objects stay shallow: only queue membership is transactional.
    Nested callers restore their own entry state, including absent queue keys.
    Usage denial evidence survives rollback for a separately revalidated
    closure; actual closure messages/tasks/updates remain transactional.
    """
    missing = object()
    snapshots = {
        key: copy(db.info[key]) if key in db.info else missing
        for key in (
            "project_execution_messages", "project_execution_recovery_tasks",
            "project_execution_deadline_tasks", "project_execution_usage_updated",
        )
    }
    try:
        async with db.begin_nested():
            yield
    except BaseException:
        for key, snapshot in snapshots.items():
            if snapshot is missing:
                db.info.pop(key, None)
            else:
                db.info[key] = snapshot
        raise


def _verification_matches(value, *, target_id, version, verdict=None):
    return (isinstance(value, dict) and type(version) is int
            and type(value.get("target_result_version")) is int
            and value["target_result_version"] == version
            and value.get("target_task_id") == target_id
            and (verdict is None or value.get("verdict") == verdict))


def logical_delegation_total_expression(execution_id: str):
    """Count real child tasks plus reserved same-task repair assignments."""
    children = select(func.count(Task.id)).where(
        Task.execution_id == execution_id, Task.parent_task_id.is_not(None),
    ).scalar_subquery()
    repairs = select(func.count(ProjectExecutionEvent.id)).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.event_type == REPAIR_EVENT,
    ).scalar_subquery()
    return children + repairs


def repair_round_total_expression(execution_id: str):
    # Input changes do not refund already consumed execution-wide rounds.
    return select(func.count(ProjectExecutionEvent.id)).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.event_type == REPAIR_EVENT,
    ).scalar_subquery()


async def _result(db, task_id, version, execution_id, revision):
    result = await db.scalar(select(TaskResult).where(
        TaskResult.task_id == task_id, TaskResult.version == version,
        TaskResult.execution_id == execution_id, TaskResult.input_revision == revision,
    ))
    if result and sha256(result.result_markdown.encode()).hexdigest() == result.result_sha256:
        return result
    return None


async def _assignment(db, execution_id, qa_result_id):
    return await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution_id,
        ProjectExecutionEvent.event_type == REPAIR_EVENT,
        ProjectExecutionEvent.event_key == f"execution:{execution_id}:qa_repair:{qa_result_id}",
    ))


def _public(row, phase, reason=None):
    return {"reservation_id": row.id, "phase": phase, "reason_code": reason,
            **deepcopy(row.details)}


async def reserve_qa_repair(db, *, qa_task_id: str) -> dict:
    """Reserve two logical units for one exact required QA failure, once.

The intent and blocked future-version QA task are real queued work. They do
not claim a producer has stopped or reopen its accepted task. Missing/unknown
native evidence keeps this reservation pending until a later advance call.
"""
    async with repair_savepoint(db):
        return await _reserve_qa_repair(db, qa_task_id=qa_task_id)


async def _reserve_qa_repair(db, *, qa_task_id: str) -> dict:
    from anygarden.project_executions.service import (
        ACTIVE_EXECUTION_STATES,
        _active,
        _event,
        _now,
        _reject,
        get_bound_task_execution_detail,
        require_execution_operation,
    )

    with db.no_autoflush:
        binding = (await db.execute(select(Task.execution_id, Task.input_revision)
            .where(Task.id == qa_task_id))).first()
        if binding is not None and binding.execution_id is not None:
            await db.execute(update(ProjectExecution).where(
                ProjectExecution.id == binding.execution_id,
            ).values(state_revision=ProjectExecution.state_revision,
                     updated_at=ProjectExecution.updated_at))
    execution = await db.get(ProjectExecution, binding.execution_id, populate_existing=True,
                             with_for_update=True) if binding and binding.execution_id else None
    qa = await db.get(Task, qa_task_id, populate_existing=True, with_for_update=True)
    if binding is not None and (qa is None or
        (qa.execution_id, qa.input_revision) != tuple(binding)):
        _reject("QA_REPAIR_EXECUTION_CHANGED", "QA input scope changed before repair reservation")
    if execution is None or qa.role != "qa" or qa.status != "done" or not qa.required_for_execution:
        _reject("QA_REPAIR_FAILURE_INVALID", "Repair requires an accepted required QA failure")
    _active(execution)
    failed = await _result(db, qa.id, qa.result_version, execution.id, execution.input_revision)
    if failed is None or (failed.verification or {}).get("verdict") != "fail":
        _reject("QA_REPAIR_FAILURE_INVALID", "Repair requires immutable completed failing findings")
    previous = await _assignment(db, execution.id, failed.id)
    if previous is not None:
        return _public(previous, "reserved")
    target = await db.scalar(select(Task).where(
        Task.id == qa.qa_target_task_id, Task.execution_id == execution.id,
        Task.input_revision == execution.input_revision,
    ).execution_options(populate_existing=True).with_for_update()) if qa.qa_target_task_id else None
    base = await _result(db, target.id, qa.qa_target_result_version, execution.id,
                         execution.input_revision) if target else None
    evidence = failed.verification or {}
    if (qa.input_revision != execution.input_revision or target is None or base is None
        or target.id == execution.root_task_id or target.role == "qa"
        or target.execution_id != execution.id or target.input_revision != execution.input_revision
        or target.status != "done" or target.result_version != base.version
        or not _verification_matches(evidence, target_id=target.id, version=base.version, verdict="fail")
        or not base.producer_agent_id or base.producer_agent_id == failed.producer_agent_id):
        _reject("QA_REPAIR_TARGET_INVALID", "Failure must identify the exact current independent target")
    require_execution_operation(execution, "repair")
    require_execution_operation(execution, "qa")
    from anygarden.project_executions.policy import repair_round_limit
    try:
        rounds = repair_round_limit(execution.limits)
    except ValueError as exc:
        _reject("INVALID_EXECUTION_LIMIT", str(exc))
    if not rounds:
        _reject("QA_REPAIR_LIMIT", "Automatic repair rounds were not granted for this execution")
    if qa.parent_task_id is None or qa.delegation_depth > execution.limits.get("max_depth", 8):
        _reject("EXECUTION_DEPTH_LIMIT", "Follow-up QA must preserve an allowed child depth")
    concurrent = await db.scalar(select(ProjectExecutionEvent.id).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.event_type == REPAIR_EVENT,
        ProjectExecutionEvent.details["base_result_id"].as_string() == base.id,
    ).limit(1))
    if concurrent:
        _reject("QA_REPAIR_TARGET_ALREADY_RESERVED", "This exact output already has a reserved repair chain")
    # Verify and copy all other frozen inputs; the old failed QA itself is
    # feedback, never an ordinary successful prerequisite or a cycle edge.
    await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=qa.id)
    other_inputs = [deepcopy(item) for item in qa.dependency_results or []
                    if item.get("task_id") != target.id]
    revision = await db.scalar(select(ExecutionInputRevision).where(
        ExecutionInputRevision.execution_id == execution.id,
        ExecutionInputRevision.revision == execution.input_revision,
    ))
    if revision is None:
        _reject("EXECUTION_INPUT_MISSING", "Immutable execution input is missing")
    reviewer = await db.get(Participant, qa.assignee_participant_id) if qa.assignee_participant_id else None
    producer = await db.get(Participant, target.assignee_participant_id) if target.assignee_participant_id else None
    qa_room = await db.get(Room, qa.room_id)
    target_room = await db.get(Room, target.room_id)
    if (reviewer is None or producer is None or reviewer.agent_id != failed.producer_agent_id
        or producer.agent_id != base.producer_agent_id or reviewer.room_id != qa.room_id
        or producer.room_id != target.room_id or reviewer.role not in AGENT_EXECUTION_ROLES
        or producer.role not in AGENT_EXECUTION_ROLES or qa_room is None or target_room is None
        or qa_room.project_id != execution.project_id or target_room.project_id != execution.project_id
        or qa_room.archived_at is not None or target_room.archived_at is not None):
        _reject("QA_REPAIR_ASSIGNEE_INVALID", "Original producer and reviewer must retain their project room roles")
    expected = base.version + 1
    spec = (
        f"Project execution {execution.id}; input revision {revision.revision}.\n"
        f"Objective:\n{revision.objective}\nOriginal constraints:\n{revision.user_constraints}\n"
        f"Recheck repaired task {target.id} at exact result version {expected}.\n"
        f"Original failing QA {qa.id}; result {failed.id}; version {failed.version}; "
        f"SHA-256 {failed.result_sha256}:\n{failed.result_markdown}\n"
        "Reproduce every original finding and related regression using the repaired artifact bodies. "
        "The original failing result is diagnostic feedback, not a successful prerequisite. "
        "Read accepted artifacts with read_project_artifact and report an actual independent pass/fail verdict.\n"
        f"Completion criteria: {revision.completion_criteria}\n"
    )
    for item in revision.input_files:
        spec += (f"\nInput file {item['filename']} (SHA-256 {item['sha256']}):\n"
                 + item.get("content", f"Base64 original bytes: {item.get('content_base64', '')}"))
    from anygarden.project_executions.usage import admission_disposition

    admission = await admission_disposition(db, execution=execution, phase="intent")
    if not admission.allowed:
        if not admission.wait and admission.reason_code in {
            "EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
        }:
            db.info.setdefault("project_execution_usage_denials", {})[execution.id] = admission.reason_code
        _reject(admission.reason_code or "EXECUTION_ADMISSION_PENDING",
                "The execution cannot reserve another repair intent")
    total = logical_delegation_total_expression(execution.id)
    changed = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.input_revision == execution.input_revision,
        ProjectExecution.state_revision == execution.state_revision,
        ProjectExecution.status.in_(ACTIVE_EXECUTION_STATES),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
        total + 2 <= execution.limits.get("max_delegations", 100),
        repair_round_total_expression(execution.id) < rounds,
        ~exists(select(ProjectExecutionEvent.id).where(
            ProjectExecutionEvent.event_key == f"execution:{execution.id}:qa_repair:{failed.id}")),
    ).values(delegation_count=total + 2, state_revision=ProjectExecution.state_revision + 1,
             plan_sealed=False, status="running", updated_at=_now()).returning(ProjectExecution.id))
    if changed is None:
        _reject("QA_REPAIR_RESERVATION_CONFLICT", "Repair budget or current execution changed before reservation")
    new_qa = Task(
        execution_id=execution.id, parent_task_id=qa.parent_task_id,
        room_id=qa.room_id, input_revision=execution.input_revision,
        delegation_depth=qa.delegation_depth, delegation_key=f"qa-repair:{failed.id}",
        title=f"Recheck {target.title} v{expected}"[:500], spec=spec, role="qa",
        required_for_execution=True, assignee_participant_id=qa.assignee_participant_id,
        qa_target_task_id=target.id, qa_target_result_version=expected,
        status="blocked", dependency_results=other_inputs or None,
        created_by=execution.owner_user_id, triggered_by="project_execution", goal_id=None,
        assigned_at=_now(),
    )
    db.add(new_qa)
    await db.flush()
    db.add(TaskBlocker(task_id=new_qa.id, blocked_by_task_id=target.id))
    await db.execute(update(ProjectExecution).where(ProjectExecution.id == execution.id).values(
        required_task_ids=[*dict.fromkeys([*execution.required_task_ids, new_qa.id])],
    ))
    round_number = (await db.scalar(select(repair_round_total_expression(execution.id)))) + 1
    reservation = await _event(db, execution,
        f"execution:{execution.id}:qa_repair:{failed.id}", REPAIR_EVENT, task_id=target.id,
        details={"input_revision": execution.input_revision,
            "qa_task_id": qa.id, "qa_result_id": failed.id, "qa_result_version": failed.version,
            "qa_result_sha256": failed.result_sha256,
            "target_task_id": target.id, "base_result_id": base.id,
            "base_result_version": base.version, "base_result_sha256": base.result_sha256,
            "expected_result_version": expected, "new_qa_task_id": new_qa.id,
            "producer_agent_id": base.producer_agent_id,
            "assignee_participant_id": target.assignee_participant_id,
            "round": round_number, "reserved_units": 2})
    if reservation is None:
        _reject("QA_REPAIR_RESERVATION_CONFLICT", "A concurrent reservation already owns this failure")
    await _event(db, execution, f"execution:{execution.id}:delegated:{new_qa.id}",
        "task_delegated", task_id=new_qa.id,
        details={"parent_task_id": new_qa.parent_task_id, "input_revision": new_qa.input_revision,
            "spec_sha256": sha256(spec.encode()).hexdigest(),
            "depends_on": [target.id, *[item["task_id"] for item in other_inputs]],
            "qa_declared_version": expected, "qa_deferred": False,
            "repair_reservation_id": reservation.id})
    db.info.setdefault("project_execution_recovery_tasks", set()).update({target.id, new_qa.id})
    await db.flush()
    return _public(reservation, "reserved")


async def _prior_native_safe(db, execution, target, base):
    attempt = await db.get(AgentTurnAttempt, base.attempt_id) if base.attempt_id else None
    turn = await db.get(AgentTurn, attempt.turn_id) if attempt else None
    if (attempt is None or turn is None or not attempt.local_execution_id
        or turn.task_id != target.id or turn.execution_id != execution.id
        or turn.execution_input_revision != target.input_revision
        or turn.room_id != target.room_id or type(attempt.attempt_number) is not int
        or attempt.attempt_number < 1 or type(attempt.generation) is not int or attempt.generation < 0
        or turn.agent_id != base.producer_agent_id or attempt.agent_id != base.producer_agent_id
        or turn.target_participant_id != target.assignee_participant_id):
        return False
    event = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id, ProjectExecutionEvent.task_id == target.id,
        ProjectExecutionEvent.event_type == "task_native_terminal",
        ProjectExecutionEvent.event_key == f"turn:{turn.request_id}:attempt:{attempt.attempt_number}:native-terminal",
    ))
    proof = event.details if event and isinstance(event.details, dict) else {}
    # record_lifecycle authenticates this unique request/attempt/generation
    # before writing the event. Its key binds the retained attempt generation;
    # no lease is copied into this public repair ledger.
    known_pair = (proof.get("outcome") == "succeeded" and proof.get("process_state") == "finished"
                  or proof.get("outcome") == "failed" and proof.get("process_state") in SAFE_PROCESS_STATES
                  or proof.get("outcome") == "cancelled" and proof.get("process_state") in {"stopped", "not_started"})
    return proof.get("local_execution_id") == attempt.local_execution_id and known_pair


async def advance_pending_repairs(db, *, execution_id: str) -> list[dict]:
    """Reevaluate reserved work after authenticated lifecycle receipt/poll.

Waiting never fences a process or guesses cleanup. Only safe exact prior
native termination permits the original task to produce its next version.
"""
    from anygarden.db.execution_approval_models import ExecutionApproval
    from anygarden.db.models import ExecutionStop
    from anygarden.messages.service import inject_task_assignment_message
    from anygarden.project_executions.authorization import SAFE_STOP_STATES
    from anygarden.project_executions.service import (
        _active,
        _event,
        _now,
        _queue_message,
        get_bound_task_execution_detail,
    )
    from anygarden.turns.service import OPEN_TURN_STATES

    execution = await db.get(ProjectExecution, execution_id, populate_existing=True, with_for_update=True)
    if execution is None or execution.status not in {"planning", "running", "waiting_children"}:
        return []
    _active(execution)
    from anygarden.project_executions.service import _reject
    # SQLite ignores FOR UPDATE. Hold the execution write fence before
    # checking native/action/task state, so an action executor cannot race
    # those reads and begin an effect before this same-task reopen.
    locked = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.input_revision == execution.input_revision,
        ProjectExecution.status.in_({"planning", "running", "waiting_children"}),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
    if locked is None:
        _reject("QA_REPAIR_EXECUTION_CHANGED", "Current execution changed before repair evaluation")
    reservations = list(await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.event_type == REPAIR_EVENT,
    ).order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id)))
    outcomes = []
    for reservation in reservations:
        details = reservation.details
        if details["input_revision"] != execution.input_revision:
            continue
        if await db.scalar(select(ProjectExecutionEvent.id).where(
            ProjectExecutionEvent.event_key == reservation.event_key + ":started")):
            continue
        target = await db.scalar(select(Task).where(
            Task.id == details["target_task_id"], Task.execution_id == execution.id,
            Task.input_revision == execution.input_revision,
        ).execution_options(populate_existing=True).with_for_update())
        base = await _result(db, details["target_task_id"], details["base_result_version"],
                             execution.id, execution.input_revision)
        if (target is None or base is None or target.status != "done"
            or target.result_version != base.version or base.id != details["base_result_id"]
            or base.result_sha256 != details["base_result_sha256"]):
            outcomes.append(_public(reservation, "waiting", "QA_REPAIR_BASE_CHANGED"))
            continue
        if not await _prior_native_safe(db, execution, target, base):
            outcomes.append(_public(reservation, "waiting", "NATIVE_STOP_UNCONFIRMED"))
            continue
        if await db.scalar(select(AgentTurn.request_id).where(
            AgentTurn.task_id == target.id, AgentTurn.state.in_(OPEN_TURN_STATES),
        ).limit(1)) or await db.scalar(select(ExecutionStop.id).where(
            ExecutionStop.execution_id == execution.id,
            ExecutionStop.status.not_in(SAFE_STOP_STATES),
        ).limit(1)):
            outcomes.append(_public(reservation, "waiting", "RECOVERY_PENDING"))
            continue
        if await db.scalar(select(ExecutionApproval.id).where(
            ExecutionApproval.execution_id == execution.id,
            or_(ExecutionApproval.task_id == target.id, ExecutionApproval.source_task_id == target.id),
            ExecutionApproval.status.in_({"executing", "unknown"}),
        ).limit(1)):
            outcomes.append(_public(reservation, "waiting", "MANAGED_EFFECT_UNCONFIRMED"))
            continue
        participant = await db.get(Participant, details["assignee_participant_id"])
        agent = await db.get(Agent, details["producer_agent_id"])
        room = await db.get(Room, target.room_id)
        if (participant is None or agent is None or room is None
            or participant.id != target.assignee_participant_id
            or participant.agent_id != base.producer_agent_id or participant.room_id != target.room_id
            or participant.role not in AGENT_EXECUTION_ROLES
            or room.project_id != execution.project_id or room.archived_at is not None
            or agent.desired_state != "running" or agent.actual_state != "running"
            or agent.pending_generation is not None):
            outcomes.append(_public(reservation, "waiting", "AGENT_STOPPED"))
            continue
        await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=target.id)
        failed = await db.get(TaskResult, details["qa_result_id"])
        if (failed is None or failed.result_sha256 != details["qa_result_sha256"]
            or sha256(failed.result_markdown.encode()).hexdigest() != failed.result_sha256):
            outcomes.append(_public(reservation, "waiting", "QA_REPAIR_FAILURE_INVALID"))
            continue
        from anygarden.project_executions.usage import admission_disposition

        admission = await admission_disposition(db, execution=execution, phase="intent")
        if not admission.allowed:
            if not admission.wait and admission.reason_code in {
                "EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED",
            }:
                db.info.setdefault("project_execution_usage_denials", {})[execution.id] = admission.reason_code
            outcomes.append(_public(reservation, "waiting",
                admission.reason_code or "EXECUTION_ADMISSION_PENDING"))
            continue
        async with repair_savepoint(db):
            # Serialize with cancellation/revision/delegation and another tick.
            changed = await db.scalar(update(ProjectExecution).where(
                ProjectExecution.id == execution.id,
                ProjectExecution.input_revision == details["input_revision"],
                ProjectExecution.status.in_({"planning", "running", "waiting_children"}),
                or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
                ~exists(select(ProjectExecutionEvent.id).where(
                    ProjectExecutionEvent.event_key == reservation.event_key + ":started")),
            ).values(state_revision=ProjectExecution.state_revision + 1, updated_at=_now())
                .returning(ProjectExecution.id))
            if changed is None:
                continue
            spec = (target.spec or "") + (
                f"\n\n**CURRENT REPAIR ASSIGNMENT**\nReservation {reservation.id}; "
                f"same task {target.id}; base result {base.id} v{base.version}, "
                f"SHA-256 {base.result_sha256}; expected new result version {details['expected_result_version']}.\n"
                f"Original failing QA result {failed.id}, SHA-256 {failed.result_sha256}:\n"
                f"{failed.result_markdown}\n"
                "Immutable failing QA artifact references (diagnostic feedback):\n"
                f"{json.dumps(failed.artifacts or [], ensure_ascii=False, sort_keys=True)}\n"
                "Repair the reproduced findings and related regression within the original constraints. "
                "Publish the repaired artifacts; submit the actual new result with mark_task_status(done). "
                "Prior results and approvals are history and do not approve new external effects."
            )
            reopened = await db.scalar(update(Task).where(
                Task.id == target.id, Task.status == "done", Task.result_version == base.version,
                Task.execution_id == execution.id, Task.input_revision == execution.input_revision,
                Task.assignee_participant_id == details["assignee_participant_id"],
            ).values(status="in_progress", finished_at=None, error=None, spec=spec,
                     goal_completion_applied=False, is_silent=False).returning(Task.id))
            if reopened is None:
                from anygarden.project_executions.service import _reject
                _reject("QA_REPAIR_TARGET_CHANGED", "Original task changed before its repair assignment")
            await db.refresh(target)
            message = await inject_task_assignment_message(db, room=room, task=target,
                                                          sender_participant_id=None, event="reassigned")
            turn = await db.scalar(select(AgentTurn).where(AgentTurn.trigger_message_id == message.id,
                AgentTurn.task_id == target.id))
            if turn is None or turn.state != "pending":
                from anygarden.project_executions.service import _reject
                _reject("QA_REPAIR_ASSIGNMENT_NOT_READY", "Repair native authorization changed before assignment")
            started = await _event(db, execution, reservation.event_key + ":started", "qa_repair_started",
                task_id=target.id, details={"reservation_id": reservation.id,
                    "input_revision": execution.input_revision, "repair_request_id": turn.request_id,
                    "assignment_message_id": message.id, "base_result_id": base.id,
                    "base_result_version": base.version, "base_result_sha256": base.result_sha256,
                    "expected_result_version": details["expected_result_version"]})
            started.message_id = message.id
            _queue_message(db, message)
            db.info.setdefault("project_execution_recovery_tasks", set()).add(target.id)
        outcomes.append(_public(reservation, "started"))
    await db.flush()
    return outcomes


async def repair_intent_for_turn(db, *, turn, attempt=None) -> dict | None:
    """Narrow recovery exception for an unfinished reserved next result.

An old accepted result is not erased. No result from this repair request may
already be accepted, including a result produced by another of its attempts.
"""
    if not turn.execution_id or not turn.task_id:
        return None
    started_rows = await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == turn.execution_id,
        ProjectExecutionEvent.task_id == turn.task_id,
        ProjectExecutionEvent.event_type == "qa_repair_started",
    ))
    started = next((row for row in started_rows
                    if row.details.get("repair_request_id") == turn.request_id), None)
    if started is None:
        return None
    reservation = await db.get(ProjectExecutionEvent, started.details["reservation_id"])
    if (reservation is None or reservation.event_type != REPAIR_EVENT
        or reservation.execution_id != turn.execution_id or reservation.task_id != turn.task_id
        or started.event_key != reservation.event_key + ":started"
        or started.message_id != started.details.get("assignment_message_id")):
        return None
    details = reservation.details
    if (reservation.event_key != f"execution:{turn.execution_id}:qa_repair:{details.get('qa_result_id')}"
        or any(started.details.get(key) != details.get(key) for key in (
            "input_revision", "base_result_id", "base_result_version",
            "base_result_sha256", "expected_result_version"))):
        return None
    task = await db.get(Task, turn.task_id)
    execution = await db.get(ProjectExecution, turn.execution_id)
    base = await _result(db, turn.task_id, details["base_result_version"], turn.execution_id,
                         turn.execution_input_revision)
    failed = await _result(db, details["qa_task_id"], details["qa_result_version"],
                           turn.execution_id, turn.execution_input_revision)
    failed_task = await db.get(Task, details["qa_task_id"])
    if (execution is None or task is None or base is None
        or failed is None or failed_task is None or failed.id != details["qa_result_id"]
        or failed.result_sha256 != details["qa_result_sha256"]
        or failed_task.execution_id != turn.execution_id
        or failed_task.input_revision != turn.execution_input_revision
        or failed_task.role != "qa" or failed_task.qa_target_task_id != task.id
        or failed_task.qa_target_result_version != base.version
        or failed.producer_agent_id == base.producer_agent_id
        or not _verification_matches(failed.verification, target_id=task.id, version=base.version, verdict="fail")
        or execution.input_revision != turn.execution_input_revision
        or task.execution_id != execution.id or task.input_revision != turn.execution_input_revision
        or details["input_revision"] != turn.execution_input_revision
        or details["target_task_id"] != task.id or base.id != details["base_result_id"]
        or details["assignee_participant_id"] != task.assignee_participant_id
        or base.result_sha256 != details["base_result_sha256"]
        or task.result_version != base.version or details["expected_result_version"] != base.version + 1
        or task.room_id != turn.room_id or task.assignee_participant_id != turn.target_participant_id
        or turn.agent_id != details["producer_agent_id"] or turn.agent_id != base.producer_agent_id
        or task.source_message_id != started.details["assignment_message_id"]
        or turn.trigger_message_id != started.details["assignment_message_id"]
        or (attempt is not None and (attempt.turn_id != turn.request_id
            or attempt.agent_id != turn.agent_id or attempt.attempt_number != turn.active_attempt))):
        return None
    accepted = await db.scalar(select(TaskResult.id).join(AgentTurnAttempt,
        AgentTurnAttempt.id == TaskResult.attempt_id).where(
            TaskResult.task_id == task.id, AgentTurnAttempt.turn_id == turn.request_id,
        ).limit(1))
    if accepted:
        return None
    return {**deepcopy(details), "reservation_id": reservation.id,
            "repair_request_id": turn.request_id,
            "assignment_message_id": started.details["assignment_message_id"]}


async def _current_pass(db, execution, failed, visited):
    if failed.id in visited:
        return None
    visited.add(failed.id)
    reservation = await _assignment(db, execution.id, failed.id)
    if reservation is None or reservation.execution_id != execution.id:
        return None
    details = reservation.details
    if details["input_revision"] != execution.input_revision or details["qa_result_sha256"] != failed.result_sha256:
        return None
    started = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.event_key == reservation.event_key + ":started"))
    target = await db.get(Task, details["target_task_id"])
    base = await _result(db, details["target_task_id"], details["base_result_version"],
                        execution.id, execution.input_revision)
    produced = await _result(db, details["target_task_id"], details["expected_result_version"],
                             execution.id, execution.input_revision)
    new_qa = await db.get(Task, details["new_qa_task_id"])
    reviewed = await _result(db, new_qa.id, new_qa.result_version, execution.id,
                             execution.input_revision) if new_qa else None
    if (started is None or target is None or base is None or produced is None or new_qa is None or reviewed is None
        or started.execution_id != execution.id or started.task_id != target.id
        or started.details.get("reservation_id") != reservation.id
        or started.message_id != started.details.get("assignment_message_id")
        or any(started.details.get(key) != details.get(key) for key in (
            "input_revision", "base_result_id", "base_result_version", "base_result_sha256", "expected_result_version"))
        or details["qa_task_id"] != failed.task_id or details["qa_result_id"] != failed.id
        or base.id != details["base_result_id"] or base.result_sha256 != details["base_result_sha256"]
        or produced.version != base.version + 1
        or not _verification_matches(failed.verification, target_id=target.id, version=base.version, verdict="fail")
        or target.execution_id != execution.id or target.input_revision != execution.input_revision
        or new_qa.execution_id != execution.id or new_qa.input_revision != execution.input_revision
        or new_qa.status != "done" or new_qa.role != "qa"
        or new_qa.qa_target_task_id != target.id or new_qa.qa_target_result_version != produced.version
        or produced.producer_agent_id != details["producer_agent_id"]
        or produced.producer_agent_id == reviewed.producer_agent_id
        or not _verification_matches(reviewed.verification, target_id=target.id, version=produced.version)):
        return None
    attempt = await db.get(AgentTurnAttempt, produced.attempt_id) if produced.attempt_id else None
    producer_turn = await db.get(AgentTurn, attempt.turn_id) if attempt else None
    if (attempt is None or producer_turn is None or attempt.turn_id != started.details["repair_request_id"]
        or producer_turn.task_id != target.id or producer_turn.execution_id != execution.id
        or producer_turn.execution_input_revision != execution.input_revision
        or producer_turn.room_id != target.room_id
        or producer_turn.target_participant_id != details["assignee_participant_id"]
        or producer_turn.agent_id != produced.producer_agent_id
        or attempt.agent_id != produced.producer_agent_id or not attempt.local_execution_id
        or type(attempt.generation) is not int or attempt.generation < 0
        or producer_turn.trigger_message_id != started.details.get("assignment_message_id")):
        return None
    review_attempt = await db.get(AgentTurnAttempt, reviewed.attempt_id) if reviewed.attempt_id else None
    review_turn = await db.get(AgentTurn, review_attempt.turn_id) if review_attempt else None
    if (review_attempt is None or review_turn is None or not review_attempt.local_execution_id
        or review_attempt.agent_id != reviewed.producer_agent_id or review_turn.agent_id != reviewed.producer_agent_id
        or review_turn.task_id != new_qa.id or review_turn.room_id != new_qa.room_id
        or review_turn.execution_id != execution.id or review_turn.execution_input_revision != execution.input_revision
        or review_turn.target_participant_id != new_qa.assignee_participant_id
        or type(review_attempt.generation) is not int or review_attempt.generation < 0):
        return None
    snapshots = [item for item in new_qa.dependency_results or []
                 if isinstance(item, dict) and item.get("task_id") == target.id]
    if len(snapshots) != 1:
        return None
    frozen = snapshots[0]
    if (frozen.get("result_id") != produced.id or type(frozen.get("result_version")) is not int
        or frozen["result_version"] != produced.version or frozen.get("result_sha256") != produced.result_sha256
        or frozen.get("execution_id") != execution.id or frozen.get("input_revision") != execution.input_revision
        or frozen.get("room_id") != target.room_id or frozen.get("artifacts") != produced.artifacts
        or not isinstance(frozen.get("result_markdown"), str)
        or sha256(frozen["result_markdown"].encode()).hexdigest() != produced.result_sha256):
        return None
    bound = await db.scalar(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id, ProjectExecutionEvent.task_id == new_qa.id,
        ProjectExecutionEvent.event_key == f"execution:{execution.id}:qa_target_bound:{new_qa.id}:r{execution.input_revision}",
        ProjectExecutionEvent.event_type == "qa_target_bound",
    ))
    if (bound is None or any(bound.details.get(key) != expected for key, expected in {
        "target_task_id": target.id, "result_id": produced.id, "result_version": produced.version,
        "result_sha256": produced.result_sha256, "producer_agent_id": produced.producer_agent_id,
        "reviewer_agent_id": reviewed.producer_agent_id, "input_revision": execution.input_revision,
    }.items())):
        return None
    from anygarden.project_executions.service import _validate_artifacts
    await _validate_artifacts(db, execution, target, list(produced.artifacts or []))
    await _validate_artifacts(db, execution, new_qa, list(reviewed.artifacts or []))
    verdict = (reviewed.verification or {}).get("verdict")
    if verdict == "pass":
        if target.status != "done" or target.result_version != produced.version:
            return None
        from anygarden.project_executions.service import (
            ExecutionConflict,
            get_bound_task_execution_detail,
        )
        try:
            # A final passing QA also consumed every inherited scout/coach
            # input at its exact still-current accepted version.
            await get_bound_task_execution_detail(db, execution_id=execution.id, task_id=new_qa.id)
        except ExecutionConflict:
            return None
        return reviewed
    if verdict == "fail":
        return await _current_pass(db, execution, reviewed, visited)
    return None


async def completed_qa_supersessions(db, *, execution_id: str) -> dict[str, str]:
    """Map only satisfied failed QA gates to their exact final passing QA.

Pending repair, a repaired result alone, unrelated passes and stale passes
never remove a gate. Repeated failing re-QA follows the bounded explicit chain.
"""
    execution = await db.get(ProjectExecution, execution_id)
    if execution is None:
        return {}
    failures = await db.scalars(select(TaskResult).join(Task, Task.id == TaskResult.task_id).where(
        TaskResult.execution_id == execution.id, TaskResult.input_revision == execution.input_revision,
        Task.role == "qa", Task.status == "done", Task.result_version == TaskResult.version,
    ))
    satisfied = {}
    for failed in failures:
        if ((failed.verification or {}).get("verdict") != "fail"
            or sha256(failed.result_markdown.encode()).hexdigest() != failed.result_sha256):
            continue
        passed = await _current_pass(db, execution, failed, set())
        if passed is not None:
            satisfied[failed.task_id] = passed.task_id
    return satisfied


async def effective_required_task_ids(db, *, execution_id: str, task_ids: list[str]) -> list[str]:
    """Finish/seal exception: preserve history, require its current replacement."""
    satisfied = await completed_qa_supersessions(db, execution_id=execution_id)
    return list(dict.fromkeys(satisfied.get(task_id, task_id) for task_id in task_ids))


async def rewire_satisfied_qa_edges(db, *, execution_id: str) -> list[str]:
    """Before resolving passing QA, move only its proved failed predecessor edges.

No completed follower or its frozen inputs are changed. General stale
dependencies must still be revalidated by readiness/finish, not silently healed.
"""
    from sqlalchemy import delete

    from anygarden.project_executions.service import _active, _now
    from anygarden.turns.service import OPEN_TURN_STATES

    execution = await db.get(ProjectExecution, execution_id, populate_existing=True, with_for_update=True)
    if execution is None or execution.status not in {"planning", "running", "waiting_children"}:
        return []
    _active(execution)
    locked = await db.scalar(update(ProjectExecution).where(
        ProjectExecution.id == execution.id,
        ProjectExecution.input_revision == execution.input_revision,
        ProjectExecution.status.in_({"planning", "running", "waiting_children"}),
        or_(ProjectExecution.deadline_at.is_(None), ProjectExecution.deadline_at > _now()),
    ).values(state_revision=ProjectExecution.state_revision).returning(ProjectExecution.id))
    if locked is None:
        return []
    satisfied = await completed_qa_supersessions(db, execution_id=execution_id)
    moved = []
    for old_id, new_id in satisfied.items():
        edges = list(await db.scalars(select(TaskBlocker).join(Task, Task.id == TaskBlocker.task_id).where(
            TaskBlocker.blocked_by_task_id == old_id, Task.execution_id == execution_id,
            Task.input_revision == execution.input_revision,
            Task.status.in_({"blocked", "todo"}), Task.result_version == 0,
            ~exists(select(TaskResult.id).where(TaskResult.task_id == Task.id)),
            ~exists(select(AgentTurn.request_id).where(
                AgentTurn.task_id == Task.id, AgentTurn.state.in_(OPEN_TURN_STATES))),
        )))
        for edge in edges:
            if edge.task_id in {old_id, new_id}:
                continue
            follower = await db.get(Task, edge.task_id, populate_existing=True)
            snapshots = list(follower.dependency_results or [])
            old_snapshots = [item for item in snapshots if isinstance(item, dict) and item.get("task_id") == old_id]
            if old_snapshots:
                old = await db.get(Task, old_id)
                old_result = await _result(db, old_id, old.result_version, execution.id,
                                           execution.input_revision) if old else None
                if (old_result is None or any(
                    item.get("result_id") != old_result.id
                    or type(item.get("result_version")) is not int
                    or item["result_version"] != old_result.version
                    or item.get("result_sha256") != old_result.result_sha256
                    for item in old_snapshots)):
                    continue
                # Only an unpublished current follower's input cache changes.
                # The resolver will capture the proved replacement after this
                # edge moves; immutable completed follower results never change.
                follower.dependency_results = [item for item in snapshots if item not in old_snapshots] or None
            if not await db.get(TaskBlocker, (edge.task_id, new_id)):
                db.add(TaskBlocker(task_id=edge.task_id, blocked_by_task_id=new_id))
            await db.execute(delete(TaskBlocker).where(TaskBlocker.task_id == edge.task_id,
                                                       TaskBlocker.blocked_by_task_id == old_id))
            moved.append(edge.task_id)
            db.info.setdefault("project_execution_recovery_tasks", set()).add(edge.task_id)
    await db.flush()
    return list(dict.fromkeys(moved))
