"""DB-only authority callback for ChannelService.commit_command.

ChannelService authenticates and authorizes BEFORE dedup and invokes this within
its channel serialization transaction. The callback never commits, sends a
message or starts a process. IDs resolve through an explicit local mirror map.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Message, Participant, Room, Task
from anygarden.federation.schemas import Principal
from anygarden.shared_channels.models import SharedMessage
from anygarden.shared_channels.schemas import (
    ChannelError,
    CommandEffect,
    command_action,
)

from .delegation_models import (
    Delegation,
    DelegationObservation,
    DelegationReservation,
    RecoveryAction,
)

TASK_STATUS = {
    "requested": "todo",
    "accepted": "in_progress",
    "running": "in_progress",
    "completed": "done",
    "failed": "failed",
    "rejected": "todo",
    "cancel_requested": "blocked",
    "cancelled": "failed",
    "unknown": "blocked",
}
TERMINAL = {"completed", "failed", "rejected", "cancelled"}
FAILURE_CODES = {
    "ENGINE_ERROR",
    "ENGINE_AUTH_ERROR",
    "PI_PROVIDER_ERROR",
    "TIMEOUT_STOPPED",
    "UNSUPPORTED_RUNTIME",
    "POLICY_DENIED",
    "AUTH_MISSING",
    "UNKNOWN_PROVIDER",
    "AUTH_CHECK_FAILED",
}


def source_task_id(
    authority_node_id: str, channel_id: str, source_message_id: str
) -> str:
    """One source-backed Task, in a namespace distinct from execution IDs."""
    return str(
        uuid5(
            NAMESPACE_URL,
            f"anygarden:shared-source-task:v1:{authority_node_id}:{channel_id}:{source_message_id}",
        )
    )


class DelegationError(ChannelError):
    def __init__(self, code: str):
        super().__init__(code, status=409)


class LateResultAfterCancel(DelegationError):
    """Only this error is eligible for separately reauthorized local auditing."""


PrincipalResolver = Callable[[AsyncSession, str, dict], Awaitable[str | None]]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DelegationService:
    def __init__(
        self,
        authority_node_id: str,
        resolve_principal: PrincipalResolver,
        *,
        executor_allowed: Callable,
    ):
        self.authority_node_id = authority_node_id
        self.resolve_principal = resolve_principal
        self.executor_allowed = executor_allowed

    def install_guards(self, channel_service) -> None:
        """Register all delegation guards; missing guards must fail closed in #591."""
        self._guarded_channel = channel_service
        for kind in (
            "task.request",
            "task.accept",
            "task.reject",
            "task.started",
            "task.result",
            "task.cancel",
            "task.cancelled",
            "task.unknown",
        ):
            old = channel_service.command_guards.get(kind)
            if old is not None and old != self.authorize_command:
                raise DelegationError("GUARD_CONFLICT")
            channel_service.command_guards[kind] = self.authorize_command
            channel_service.effects[kind] = self.apply_effect

    def install_submitters(self, channel_service) -> None:
        """Register the task coordinator for #591's ``submitters`` registry.

        ``ChannelService.submit`` looks the kind up before opening its default
        transaction and hands the whole transaction lifecycle to the coordinator.
        """
        service = self

        async def submit(envelope, *, tls):
            return await service.submit(channel_service, envelope, tls=tls)

        for kind in (
            "task.request",
            "task.accept",
            "task.reject",
            "task.started",
            "task.result",
            "task.cancel",
            "task.cancelled",
            "task.unknown",
        ):
            old = channel_service.submitters.get(kind)
            if (
                old is not None
                and getattr(old, "__delegation_owner__", None) is not self
            ):
                raise DelegationError("SUBMITTER_CONFLICT")
            submit.__delegation_owner__ = self
            channel_service.submitters[kind] = submit

    async def submit(self, channel_service, envelope, *, tls):
        """Own the commit/rollback boundary for a task HTTP/transport coordinator."""
        try:
            async with channel_service.sessions.begin() as db:
                return await channel_service.commit_command(
                    db, envelope, self.apply_effect, tls=tls
                )
        except LateResultAfterCancel:

            async def reauthorize(db, command):
                # Same current-grant boundary as ChannelService authorization,
                # through the public PeerService.authorize entry and the same
                # shared kind→action mapping.
                action = command_action(command.get("kind", "channel.read"))
                if channel_service.peers is None:
                    raise ChannelError("PEERING_DISABLED", 503)
                await channel_service.peers.authorize(
                    db,
                    tls,
                    sender_node_id=command["sender_node_id"],
                    authority_node_id=command["authority_node_id"],
                    channel_id=command["channel_id"],
                    principal=Principal(**command["actor"]),
                    action=action,
                    grant_epoch=command["grant_epoch"],
                )
                await self.authorize_command(db, command)

            async with channel_service.sessions.begin() as db:
                await self.record_late_result(db, envelope, reauthorize=reauthorize)
            raise

    async def _participant(self, db, channel_id, principal) -> Participant:
        participant_id = await self.resolve_principal(db, channel_id, principal)
        participant = await db.scalar(
            select(Participant)
            .where(
                Participant.id == participant_id,
                Participant.room_id == channel_id,
                Participant.role.in_(("member", "admin", "owner")),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if participant is None:
            raise DelegationError("PRINCIPAL_DENIED")
        return participant

    async def authorize_command(self, db: AsyncSession, envelope: dict) -> Participant:
        """Run in the caller's transaction BEFORE receipt dedup, after peer auth.

        The peer/channel layer still owns grant/export capability checks. This
        additional local mirror check fences role removal and room archival.
        SQLite callers must have obtained their write reservation before reads.
        """
        # Guards without the task submitter would let normal commands run on
        # the default single-transaction path while losing the late-result
        # audit; refuse that misconfiguration instead (fail closed, like a
        # missing guard).
        guarded = getattr(self, "_guarded_channel", None)
        if guarded is not None:
            submitter = guarded.submitters.get(envelope["kind"])
            if getattr(submitter, "__delegation_owner__", None) is not self:
                raise DelegationError("SUBMITTER_REQUIRED")
        if envelope["authority_node_id"] != self.authority_node_id:
            raise DelegationError("AUTHORITY_MISMATCH")
        channel_id = envelope["channel_id"]
        if (
            await db.scalar(
                select(Room.id)
                .where(Room.id == channel_id, Room.archived_at.is_(None))
                .with_for_update()
            )
            is None
        ):
            raise DelegationError("CHANNEL_DENIED")
        actor = await self._participant(db, channel_id, envelope["actor"])
        if envelope["kind"] == "task.request":
            executor = envelope["payload"]["executor"]
            if not await self.executor_allowed(db, channel_id, executor):
                raise DelegationError("EXECUTOR_DENIED")
            await self._participant(
                db,
                channel_id,
                {
                    "node_id": executor["node_id"],
                    "kind": "agent",
                    "principal_id": executor["agent_id"],
                },
            )
        elif envelope["kind"] not in {"task.cancel"}:
            principal = envelope["actor"]
            if principal["kind"] != "agent" or not await self.executor_allowed(
                db,
                channel_id,
                {
                    "node_id": principal["node_id"],
                    "agent_id": principal["principal_id"],
                },
            ):
                raise DelegationError("EXECUTOR_DENIED")
        return actor

    async def apply_effect(self, db: AsyncSession, envelope: dict) -> CommandEffect:
        if envelope["authority_node_id"] != self.authority_node_id:
            raise DelegationError("AUTHORITY_MISMATCH")
        channel_id, payload, kind = (
            envelope["channel_id"],
            envelope["payload"],
            envelope["kind"],
        )
        if kind not in {
            "task.request",
            "task.accept",
            "task.reject",
            "task.started",
            "task.result",
            "task.cancel",
            "task.cancelled",
            "task.unknown",
        }:
            raise DelegationError("INVALID_KIND")
        actor = await self.authorize_command(db, envelope)
        if kind == "task.request":
            return await self._request(db, envelope)
        record = await db.scalar(
            select(Delegation)
            .where(
                Delegation.id == payload["delegation_id"],
                Delegation.channel_id == channel_id,
                Delegation.authority_node_id == self.authority_node_id,
            )
            .execution_options(populate_existing=True)
        )
        if record is None:
            raise DelegationError("DELEGATION_MISSING")
        executor = {
            "node_id": record.executor_node_id,
            "kind": "agent",
            "principal_id": record.executor_agent_id,
        }
        if kind == "task.cancel":
            if envelope["actor"] != record.requester and actor.role not in {
                "admin",
                "owner",
            }:
                raise DelegationError("ACTOR_DENIED")
        elif (
            envelope["actor"] != executor or actor.id != record.executor_participant_id
        ):
            raise DelegationError("ACTOR_DENIED")
        if kind == "task.result" and record.state in {"cancel_requested", "cancelled"}:
            if payload["execution_id"] != record.execution_id:
                raise DelegationError("EXECUTION_MISMATCH")
            raise LateResultAfterCancel(
                "CANCEL_PENDING" if record.state == "cancel_requested" else "TERMINAL"
            )
        if payload["expected_revision"] != record.revision:
            raise DelegationError("REVISION_CONFLICT")
        if record.state in TERMINAL:
            raise DelegationError("TERMINAL")
        if record.state == "unknown" and kind != "task.cancel":
            raise DelegationError("RECONCILE_REQUIRED")
        transitions = {
            "task.accept": ({"requested"}, "accepted"),
            "task.reject": ({"requested"}, "rejected"),
            "task.started": ({"accepted"}, "running"),
            "task.result": ({"accepted", "running"}, "completed"),
            "task.cancel": (
                {"requested", "accepted", "running", "unknown"},
                "cancel_requested",
            ),
            "task.cancelled": ({"cancel_requested"}, "cancelled"),
            "task.unknown": ({"accepted", "running", "cancel_requested"}, "unknown"),
        }
        before = record.state
        allowed, target = transitions[kind]
        if before not in allowed:
            raise DelegationError("STATE_CONFLICT")
        execution_id = record.execution_id
        process = record.process_state
        if kind == "task.accept":
            execution_id = payload["execution_id"]
            process = "unknown"
        elif "execution_id" in payload:
            if execution_id is None:
                if (
                    kind != "task.cancelled"
                    or payload.get("process_state") != "not_started"
                ):
                    raise DelegationError("EXECUTION_MISMATCH")
            elif payload["execution_id"] != execution_id:
                raise DelegationError("EXECUTION_MISMATCH")
        if kind == "task.started":
            process = "running"
        elif kind == "task.result":
            if payload["outcome"] == "failed":
                if payload["error_code"] not in FAILURE_CODES:
                    raise DelegationError("INVALID_FAILURE")
                target = "failed"
            elif payload["outcome"] != "succeeded" or not isinstance(
                payload.get("text"), str
            ):
                raise DelegationError("INVALID_RESULT")
            process = "finished"
        elif kind == "task.unknown":
            process = "unknown"
        elif kind == "task.cancelled":
            process = payload["process_state"]
            if process not in {"not_started", "stopped"} or (
                process == "not_started" and record.process_state == "running"
            ):
                raise DelegationError("STATE_CONFLICT")
        values = {"status": TASK_STATUS[target]}
        now = datetime.now(UTC)
        if target == "accepted":
            values.update(started_at=now, finished_at=None, error=None)
        if target == "rejected":
            values.update(assignee_participant_id=None, assigned_at=None)
        if target in {"completed", "failed", "cancelled"}:
            values.update(finished_at=now)
        if target == "completed":
            values.update(result_markdown=payload["text"], error=None)
        if target == "failed":
            values.update(error=payload["error_code"], result_markdown=None)
        if target == "cancelled":
            values.update(error="CANCELLED", result_markdown=None)
        changed = await db.execute(
            update(Delegation)
            .where(
                Delegation.id == record.id,
                Delegation.revision == record.revision,
                Delegation.state == before,
            )
            .values(
                state=target,
                revision=record.revision + 1,
                execution_id=execution_id,
                process_state=process,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise DelegationError("REVISION_CONFLICT")
        task_change = await db.execute(
            update(Task)
            .where(
                Task.id == record.task_id,
                Task.room_id == channel_id,
                Task.status == TASK_STATUS[before],
                Task.assignee_participant_id == record.executor_participant_id,
                self._live_actor(actor.id, channel_id),
                self._live_room(channel_id),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if task_change.rowcount != 1:
            raise DelegationError("TASK_CONFLICT")
        if target == "rejected":
            await db.execute(
                delete(DelegationReservation).where(
                    DelegationReservation.task_id == record.task_id,
                    DelegationReservation.delegation_id == record.id,
                )
            )
        return CommandEffect(record.revision + 1, target, process, TASK_STATUS[target])

    @staticmethod
    def _live_room(channel_id):
        return exists(
            select(Room.id).where(Room.id == channel_id, Room.archived_at.is_(None))
        )

    @staticmethod
    def _live_actor(participant_id, channel_id):
        return exists(
            select(Participant.id).where(
                Participant.id == participant_id,
                Participant.room_id == channel_id,
                Participant.role.in_(("member", "admin", "owner")),
            )
        )

    async def _request(self, db, envelope):
        p, channel_id = envelope["payload"], envelope["channel_id"]
        if p["expected_revision"] != 0:
            raise DelegationError("REVISION_CONFLICT")
        source = await db.get(
            SharedMessage, (self.authority_node_id, channel_id, p["source_message_id"])
        )
        if source is None or source.thread_root_id is not None:
            raise DelegationError("SOURCE_DENIED")
        executor = await self._participant(
            db,
            channel_id,
            {
                "node_id": p["executor"]["node_id"],
                "kind": "agent",
                "principal_id": p["executor"]["agent_id"],
            },
        )
        actor = await self._participant(db, channel_id, envelope["actor"])
        if await db.get(Delegation, p["delegation_id"]) or await db.get(
            DelegationReservation, p["task_id"]
        ):
            raise DelegationError("CLAIM_CONFLICT")
        # The product facade requests this derived ID. Creation belongs in
        # the same serialized authority transaction as claim/event/receipt;
        # arbitrary task IDs retain the existing claim-only contract.
        if (
            p["task_id"]
            == source_task_id(
                self.authority_node_id, channel_id, p["source_message_id"]
            )
            and await db.get(Task, p["task_id"]) is None
        ):
            message = await db.get(Message, source.local_message_id)
            existing_source = await db.scalar(
                select(Task.id).where(Task.source_message_id == source.local_message_id)
            )
            if (
                message is None
                or message.room_id != channel_id
                or message.parent_message_id is not None
                or existing_source is not None
            ):
                raise DelegationError("CLAIM_CONFLICT")
            text = message.content
            db.add(
                Task(
                    id=p["task_id"],
                    room_id=channel_id,
                    source_message_id=source.local_message_id,
                    title=(text.strip().splitlines() or ["Delegated task"])[0][:500],
                    spec=text,
                    status="todo",
                    created_by=(
                        actor.user_id
                        if envelope["actor"]["node_id"] == self.authority_node_id
                        else None
                    ),
                )
            )
            await db.flush()
        result = await db.execute(
            update(Task)
            .where(
                Task.id == p["task_id"],
                Task.room_id == channel_id,
                Task.source_message_id == source.local_message_id,
                Task.status == "todo",
                Task.assignee_participant_id.is_(None),
                exists(
                    select(Message.id).where(
                        Message.id == source.local_message_id,
                        Message.room_id == channel_id,
                        Message.parent_message_id.is_(None),
                    )
                ),
                self._live_room(channel_id),
                self._live_actor(actor.id, channel_id),
                self._live_actor(executor.id, channel_id),
            )
            .values(assignee_participant_id=executor.id, assigned_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            raise DelegationError("CLAIM_CONFLICT")
        record = Delegation(
            id=p["delegation_id"],
            authority_node_id=self.authority_node_id,
            channel_id=channel_id,
            task_id=p["task_id"],
            source_message_id=p["source_message_id"],
            requester=envelope["actor"],
            executor_node_id=p["executor"]["node_id"],
            executor_agent_id=p["executor"]["agent_id"],
            executor_participant_id=executor.id,
            execution_id=None,
            state="requested",
            revision=1,
            process_state="not_started",
        )
        db.add(record)
        await db.flush()
        db.add(DelegationReservation(task_id=p["task_id"], delegation_id=record.id))
        await db.flush()
        return CommandEffect(1, "requested", "not_started", "todo")

    async def sweep_pickup_timeouts(
        self, channel_service, *, actor: dict, now: datetime, timeout: timedelta
    ) -> dict:
        """Finalize pickup-timeout delegations as channel-admin cancels (#58).

        For every ``requested`` delegation older than ``timeout`` on this
        authority, emit one ``task.cancel`` through the channel log under the
        given admin actor — follower mirrors converge through the normal
        event path — and record ``PICKUP_TIMEOUT`` in the local audit table.
        Each candidate runs in its own transaction with a deterministic
        request id, so re-running the sweep is idempotent. Rows the guard
        refuses (e.g. the admin lost room membership) are skipped and
        reported with the refusal code; they are never force-finalized.
        Terminal ``cancelled`` still requires the executor's stop
        confirmation per contract; timed-out requests rest at
        ``cancel_requested`` (Task ``blocked``).
        """
        cutoff = now - timeout
        results: dict[str, list] = {"finalized": [], "skipped": []}
        async with channel_service.sessions() as db:
            candidates = (
                await db.execute(
                    select(Delegation.id, Delegation.channel_id).where(
                        Delegation.authority_node_id == self.authority_node_id,
                        Delegation.state == "requested",
                        Delegation.created_at <= cutoff,
                    )
                )
            ).all()
        for delegation_id, channel_id in candidates:
            request_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"pickup-timeout:{self.authority_node_id}:"
                    f"{channel_id}:{delegation_id}",
                )
            )
            async with channel_service.sessions.begin() as db:
                record = await db.get(Delegation, delegation_id, populate_existing=True)
                if (
                    record is None
                    or record.state != "requested"
                    or _utc(record.created_at) > cutoff
                ):
                    continue  # raced with a live accept/cancel between passes
                envelope = {
                    "protocol_version": 1,
                    "request_id": request_id,
                    "sender_node_id": self.authority_node_id,
                    "authority_node_id": self.authority_node_id,
                    "channel_id": channel_id,
                    "grant_epoch": 1,
                    "actor": actor,
                    "kind": "task.cancel",
                    "payload": {
                        "delegation_id": delegation_id,
                        "expected_revision": record.revision,
                    },
                }
                try:
                    # Authority-internal action: the channel log and the Task /
                    # delegation effect commit together, exactly like any
                    # other command; the stable request id dedups re-runs.
                    await channel_service._commit_authorized(
                        db, envelope, self.apply_effect
                    )
                except ChannelError as error:
                    await db.rollback()
                    async with channel_service.sessions.begin() as audit:
                        audit.add(
                            DelegationObservation(
                                id=str(uuid4()),
                                delegation_id=delegation_id,
                                request_id=request_id,
                                execution_id=None,
                                reason=error.code[:32],
                            )
                        )
                    results["skipped"].append(
                        {"delegation_id": delegation_id, "code": error.code}
                    )
                    continue
                db.add(
                    DelegationObservation(
                        id=str(uuid4()),
                        delegation_id=delegation_id,
                        request_id=request_id,
                        execution_id=None,
                        reason="PICKUP_TIMEOUT",
                    )
                )
                await db.flush()
                results["finalized"].append(
                    {"delegation_id": delegation_id, "channel_id": channel_id}
                )
        return results

    async def record_late_result(
        self, db: AsyncSession, envelope: dict, *, reauthorize: Callable
    ) -> bool:
        """Call ONLY in a new transaction after LateResultAfterCancel rollback.

        reauthorize must repeat current peer/grant/principal checks and acquire
        the same grant serialization boundary as the main command transaction.
        """
        await reauthorize(db, envelope)
        if (
            envelope["kind"] != "task.result"
            or envelope["authority_node_id"] != self.authority_node_id
        ):
            raise DelegationError("INVALID_OBSERVATION")
        p = envelope["payload"]
        record = await db.get(Delegation, p["delegation_id"], populate_existing=True)
        if (
            record is None
            or record.channel_id != envelope["channel_id"]
            or record.authority_node_id != self.authority_node_id
            or record.state not in {"cancel_requested", "cancelled"}
            or record.execution_id != p["execution_id"]
            or envelope["actor"]
            != {
                "node_id": record.executor_node_id,
                "kind": "agent",
                "principal_id": record.executor_agent_id,
            }
        ):
            raise DelegationError("INVALID_OBSERVATION")
        if (
            await db.scalar(
                select(Room.id).where(
                    Room.id == record.channel_id, Room.archived_at.is_(None)
                )
            )
            is None
        ):
            raise DelegationError("CHANNEL_DENIED")
        actor = await self._participant(db, record.channel_id, envelope["actor"])
        if actor.id != record.executor_participant_id:
            raise DelegationError("PRINCIPAL_DENIED")
        key = str(
            uuid5(NAMESPACE_URL, f"late-result:{record.id}:{envelope['request_id']}")
        )
        if await db.get(DelegationObservation, key):
            return False
        db.add(
            DelegationObservation(
                id=key,
                delegation_id=record.id,
                request_id=envelope["request_id"],
                execution_id=p["execution_id"],
                reason="LATE_RESULT_AFTER_CANCEL",
            )
        )
        await db.flush()
        return True

    # ── D-3 (#626): role/fit auto-selection with availability filters ──

    async def select_executor(
        self,
        db: AsyncSession,
        *,
        channel_id: str,
        now: datetime | None = None,
        exclude: frozenset[tuple[str, str]] = frozenset(),
    ) -> dict | None:
        """Deterministically pick an executor from the active roster.

        ``exclude`` drops ``(node_id, agent_id)`` pairs — used by reassignment
        (D-4b) to route around the executor that just failed.

        Candidates are active agent participants with a fenced role. Each
        must clear the current grant boundary (``executor_allowed``); local
        agents additionally clear the D-2 availability predicate and a
        budget pause. The tie-break prefers the fewest active delegations,
        then participant order — stable and auditable. Returns ``None``
        when nobody qualifies (callers must fail explicitly, no fallback).
        """
        from anygarden.agent_availability import routing_blocked
        from anygarden.db.models import Agent as AgentRow
        from anygarden.shared_channels.models import SharedParticipant

        moment = now or datetime.now(UTC)
        roster = (
            await db.scalars(
                select(SharedParticipant).where(
                    SharedParticipant.authority_node_id == self.authority_node_id,
                    SharedParticipant.channel_id == channel_id,
                    SharedParticipant.kind == "agent",
                    SharedParticipant.active.is_(True),
                )
            )
        ).all()
        scored: list[tuple[int, str, dict]] = []
        for entry in sorted(roster, key=lambda e: (e.node_id, e.principal_id)):
            executor = {"node_id": entry.node_id, "agent_id": entry.principal_id}
            if (entry.node_id, entry.principal_id) in exclude:
                continue
            if not await self.executor_allowed(db, channel_id, executor):
                continue
            if entry.node_id == self.authority_node_id:
                agent = await db.get(AgentRow, entry.principal_id)
                if (
                    agent is None
                    or routing_blocked(agent, now=moment)
                    or agent.pause_reason == "budget"
                ):
                    continue
            active = await db.scalar(
                select(func.count())
                .select_from(Delegation)
                .where(
                    Delegation.authority_node_id == self.authority_node_id,
                    Delegation.channel_id == channel_id,
                    Delegation.executor_node_id == entry.node_id,
                    Delegation.executor_agent_id == entry.principal_id,
                    Delegation.state.in_(("requested", "accepted", "running")),
                )
            )
            scored.append(
                (int(active or 0), f"{entry.node_id}:{entry.principal_id}", executor)
            )
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1]))
        return scored[0][2]

    async def delegate(
        self,
        channel_service,
        *,
        channel_id: str,
        task_id: str,
        source_message_id: str,
        requester: dict,
        tls,
        executor: dict | None = None,
        now: datetime | None = None,
        exclude: frozenset[tuple[str, str]] = frozenset(),
    ) -> dict:
        """Product entry: select (unless explicit) then issue task.request.

        The wire contract is unchanged — auto-selection only fills the
        executor field before the existing transactional command path.
        """
        from anygarden.federation.models import PeerGrant

        chosen = executor
        if chosen is None:
            async with channel_service.sessions() as db:
                chosen = await self.select_executor(
                    db, channel_id=channel_id, now=now, exclude=exclude
                )
        if chosen is None:
            raise DelegationError("NO_ELIGIBLE_EXECUTOR")
        async with channel_service.sessions() as db:
            grant = await db.get(
                PeerGrant,
                (requester["node_id"], self.authority_node_id, channel_id),
            )
            grant_epoch = grant.epoch if grant is not None else 1
        envelope = {
            "protocol_version": 1,
            "request_id": str(uuid4()),
            "sender_node_id": requester["node_id"],
            "authority_node_id": self.authority_node_id,
            "channel_id": channel_id,
            "grant_epoch": grant_epoch,
            "actor": requester,
            "kind": "task.request",
            "payload": {
                "delegation_id": str(uuid4()),
                "expected_revision": 0,
                "task_id": task_id,
                "source_message_id": source_message_id,
                "executor": chosen,
            },
        }
        receipt = await self.submit(channel_service, envelope, tls=tls)
        receipt = dict(receipt)
        receipt["selected_executor"] = chosen if executor is None else None
        return receipt

    async def reassign(
        self,
        channel_service,
        *,
        delegation_id: str,
        requester: dict,
        tls,
        exclude: frozenset[tuple[str, str]] | None = None,
        now: datetime | None = None,
    ) -> dict:
        """Re-delegate a rejected delegation to an alternative executor (D-4b).

        Only ``rejected`` delegations qualify: rejection already returned the
        Task to ``todo`` and released the reservation, so the new request is
        an ordinary transactional command — receipts, duplicate prevention
        and audit inherit untouched. The failing executor is excluded by
        default; callers may add more. A ``TRANSITIONED`` observation links
        the old delegation to the new one. No alternative → structured
        ``NO_ALTERNATIVE_EXECUTOR`` after an auditable observation.
        """
        async with channel_service.sessions() as db:
            record = await db.get(Delegation, delegation_id)
            if record is None or record.authority_node_id != self.authority_node_id:
                raise DelegationError("DELEGATION_MISSING")
            if record.state != "rejected":
                raise DelegationError("STATE_CONFLICT")
            # D-4b P3: reassignment is original-requester self-service —
            # cancel control and the new delegation stay with whoever asked.
            if record.requester != requester:
                raise DelegationError("REQUESTER_MISMATCH")
            task = await db.get(Task, record.task_id)
            if task is None or task.status != "todo":
                raise DelegationError("STATE_CONFLICT")
            channel_id = record.channel_id
            # D-5 cycle defense: exclude EVERY executor this task ever had,
            # not just the one that just failed — A→B→A ping-pong and long
            # cycles across reassignments are structurally impossible then.
            prior = (
                await db.execute(
                    select(
                        Delegation.executor_node_id, Delegation.executor_agent_id
                    ).where(
                        Delegation.authority_node_id == self.authority_node_id,
                        Delegation.channel_id == channel_id,
                        Delegation.task_id == record.task_id,
                    )
                )
            ).all()
            excluded = frozenset(prior) | (exclude or frozenset())
        try:
            receipt = await self.delegate(
                channel_service,
                channel_id=channel_id,
                task_id=record.task_id,
                source_message_id=record.source_message_id,
                requester=requester,
                tls=tls,
                exclude=excluded,
                now=now,
            )
        except DelegationError as error:
            if error.code != "NO_ELIGIBLE_EXECUTOR":
                raise
            # Structured alert (D-4b ③): every alternative is exhausted.
            async with channel_service.sessions.begin() as db:
                db.add(
                    DelegationObservation(
                        id=str(uuid4()),
                        delegation_id=delegation_id,
                        request_id=str(
                            uuid5(NAMESPACE_URL, f"no-alternative:{delegation_id}")
                        ),
                        execution_id=None,
                        reason="NO_ALTERNATIVE",
                    )
                )
            # D-5 escalation: every alternative (and every prior executor)
            # is exhausted — a human must look at this task.
            async with channel_service.sessions.begin() as db:
                db.add(
                    RecoveryAction(
                        id=str(uuid4()),
                        type="ESCALATION",
                        authority_node_id=self.authority_node_id,
                        channel_id=channel_id,
                        delegation_id=delegation_id,
                        task_id=record.task_id,
                        target=dict(requester),
                        payload={
                            "reason": "NO_ALTERNATIVE_EXECUTOR",
                            "excluded": sorted(f"{n}:{a}" for n, a in excluded),
                        },
                        state="pending",
                    )
                )
            raise DelegationError("NO_ALTERNATIVE_EXECUTOR") from error
        async with channel_service.sessions.begin() as db:
            db.add(
                DelegationObservation(
                    id=str(uuid4()),
                    delegation_id=delegation_id,
                    request_id=str(
                        uuid5(NAMESPACE_URL, f"transitioned:{delegation_id}")
                    ),
                    execution_id=None,
                    reason="TRANSITIONED",
                )
            )
            # D-5 owner-return: the original requester is told where the
            # work went (a notification consumer drains these actions).
            db.add(
                RecoveryAction(
                    id=str(uuid4()),
                    type="OWNER_RETURN",
                    authority_node_id=self.authority_node_id,
                    channel_id=channel_id,
                    delegation_id=delegation_id,
                    task_id=record.task_id,
                    target=dict(requester),
                    payload={
                        "new_request_id": receipt["request_id"],
                        "selected_executor": receipt.get("selected_executor"),
                    },
                    state="pending",
                )
            )
        receipt = dict(receipt)
        receipt["transitioned_from"] = delegation_id
        return receipt

    async def chain_health(
        self, db: AsyncSession, *, channel_id: str, now: datetime | None = None
    ) -> dict:
        """Org-chain health snapshot for a channel (D-5, #628).

        Prerequisite check for multi-home extension: who could take work
        right now, who is blocked, and how much transition traffic the
        chain has produced. Read-only.
        """
        from anygarden.agent_availability import routing_blocked
        from anygarden.db.models import Agent as AgentRow
        from anygarden.shared_channels.models import SharedParticipant

        moment = now or datetime.now(UTC)
        roster = (
            await db.scalars(
                select(SharedParticipant).where(
                    SharedParticipant.authority_node_id == self.authority_node_id,
                    SharedParticipant.channel_id == channel_id,
                    SharedParticipant.kind == "agent",
                    SharedParticipant.active.is_(True),
                )
            )
        ).all()
        eligible: list[dict] = []
        blocked: list[dict] = []
        for entry in sorted(roster, key=lambda e: (e.node_id, e.principal_id)):
            executor = {"node_id": entry.node_id, "agent_id": entry.principal_id}
            reason = None
            if not await self.executor_allowed(db, channel_id, executor):
                reason = "grant"
            elif entry.node_id == self.authority_node_id:
                agent = await db.get(AgentRow, entry.principal_id)
                if agent is None:
                    reason = "missing_agent_row"
                elif routing_blocked(agent, now=moment):
                    reason = agent.unavailable_code or "unavailable"
                elif agent.pause_reason == "budget":
                    reason = "budget_pause"
            if reason is None:
                eligible.append(executor)
            else:
                blocked.append({**executor, "reason": reason})
        transitions = await db.scalar(
            select(func.count())
            .select_from(DelegationObservation)
            .where(DelegationObservation.reason == "TRANSITIONED")
        )
        return {
            "channel_id": channel_id,
            "eligible_executors": eligible,
            "blocked_executors": blocked,
            "transitioned_total": int(transitions or 0),
            "healthy": bool(eligible),
        }
