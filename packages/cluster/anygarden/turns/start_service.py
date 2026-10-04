"""Commit an exact native invocation permit before answering its room socket."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from anygarden.db.models import ActivityLog, AgentTurn, AgentTurnAttempt
from anygarden.project_executions.service import (
    ExecutionConflict,
    TurnProof,
    authorize_turn,
)
from anygarden.turns.service import (
    ACTIVE_ATTEMPT_STATES,
    _execution_gate,
    _workspace_gate,
)
from anygarden.ws.protocol import TurnStartPermitOut


async def authorize_native_start(db, *, agent_id: str, room_id: str,
                                 participant_id: str, packet) -> TurnStartPermitOut:
    reply = dict(room_id=room_id, request_id=packet.request_id,
                 attempt=packet.attempt, generation=packet.generation,
                 local_execution_id=packet.local_execution_id,
                 execution_id=packet.execution_id, input_revision=packet.input_revision)
    try:
        # Reservation, attempt binding and permit audit commit together. A
        # rejected reservation must not leave a consumed counter or native ID.
        async with db.begin_nested():
            if str(UUID(packet.local_execution_id)) != packet.local_execution_id:
                raise ExecutionConflict("LOCAL_EXECUTION_ID_INVALID", "Canonical invocation ID required")
            turn = await authorize_turn(db, agent_id=agent_id, proof=TurnProof(
                packet.request_id, packet.attempt, packet.generation, packet.lease))
            if turn.room_id != room_id or turn.target_participant_id != participant_id:
                raise ExecutionConflict("TURN_SCOPE_MISMATCH", "Start must use the delivered room participant")
            if (turn.execution_id != packet.execution_id
                or turn.execution_input_revision != packet.input_revision):
                raise ExecutionConflict("TURN_EXECUTION_BINDING_MISMATCH", "Start must use its immutable delivered revision")
            workspace_ok, workspace_reason, _ = await _workspace_gate(db, turn)
            if not workspace_ok:
                raise ExecutionConflict(workspace_reason or "WORKSPACE_AUTHORIZATION_REVOKED", "Workspace access revoked")
            allowed, code, snapshot = await _execution_gate(db, turn, lock=True)
            if not allowed:
                raise ExecutionConflict(code or "EXECUTION_AUTHORIZATION_REVOKED", "Execution scope is no longer current")
            attempt = await db.scalar(select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == turn.request_id,
                AgentTurnAttempt.attempt_number == packet.attempt,
            ).execution_options(populate_existing=True).with_for_update())
            if attempt is None:
                raise ExecutionConflict("ATTEMPT_NOT_FOUND", "Delivered attempt missing")
            if attempt.local_execution_id not in (None, packet.local_execution_id):
                raise ExecutionConflict("LOCAL_EXECUTION_BINDING_IMMUTABLE", "This attempt already permitted one native invocation")
            if attempt.local_execution_id is None:
                from anygarden.project_executions.service import (
                    validate_parent_handoff_turn,
                )

                handoff_allowed, handoff_reason = await validate_parent_handoff_turn(db, turn)
                if not handoff_allowed:
                    raise ExecutionConflict(handoff_reason or "PARENT_HANDOFF_NOT_CURRENT",
                                            "The parent handoff is no longer current")
            from anygarden.project_executions.usage import reserve_native_invocation

            admission = await reserve_native_invocation(
                db, turn=turn, attempt=attempt,
                local_execution_id=packet.local_execution_id,
            )
            if not admission.allowed:
                raise ExecutionConflict(admission.reason_code or "NATIVE_ADMISSION_DENIED",
                                        "The execution cannot admit another native invocation")
            changed = await db.scalar(update(AgentTurnAttempt).where(
                AgentTurnAttempt.id == attempt.id,
                AgentTurnAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                AgentTurnAttempt.generation == packet.generation,
                AgentTurnAttempt.lease_token == packet.lease,
                AgentTurnAttempt.lease_expires_at > datetime.now(UTC),
                or_(AgentTurnAttempt.local_execution_id.is_(None),
                    AgentTurnAttempt.local_execution_id == packet.local_execution_id),
                select(AgentTurn.request_id).where(
                    AgentTurn.request_id == turn.request_id,
                    AgentTurn.active_attempt == packet.attempt,
                    AgentTurn.state.in_({"pending", "leased", "retrying"}),
                ).exists(),
            ).values(local_execution_id=packet.local_execution_id).returning(AgentTurnAttempt.id))
            if changed is None:
                raise ExecutionConflict("NATIVE_START_FENCE_CHANGED", "Start authorization changed before permit")
            if not admission.duplicate:
                db.add(ActivityLog(agent_id=agent_id, room_id=room_id,
                               request_id=turn.request_id, event_type="turn_start_permitted",
                               details={"attempt": packet.attempt, "generation": packet.generation,
                                        "execution_id": turn.execution_id,
                                        "input_revision": turn.execution_input_revision,
                                        "local_execution_id": packet.local_execution_id}))
        return TurnStartPermitOut(**reply, allowed=True, input_snapshot=snapshot)
    except (ExecutionConflict, ValueError) as exc:
        code = getattr(exc, "code", "LOCAL_EXECUTION_ID_INVALID")
        if code in {"EXECUTION_NATIVE_INVOCATION_LIMIT", "EXECUTION_USAGE_LIMIT_REACHED"}:
            from anygarden.project_executions.limits import close_execution_limit

            # The admission savepoint has rolled back. Preserve the automatic
            # closure in the caller's transaction before returning the denial.
            await close_execution_limit(db, execution_id=packet.execution_id,
                                        reason_code=code)
        return TurnStartPermitOut(**reply, allowed=False, code=code)
    except IntegrityError:
        # Concurrent immutable identity collisions are closed denials. The
        # savepoint also rolled back the counter increment and attempt UUID.
        return TurnStartPermitOut(**reply, allowed=False,
                                  code="NATIVE_START_FENCE_CHANGED")
