"""DB-only follower task projection; no scheduling and no local Task mutation."""

from sqlalchemy import select

from anygarden.shared_channels.models import SharedMessage
from anygarden.shared_channels.schemas import validate

from .delegation import TASK_STATUS, DelegationError
from .delegation_models import DelegationMirror

KINDS = (
    "task.request",
    "task.accept",
    "task.reject",
    "task.started",
    "task.result",
    "task.cancel",
    "task.cancelled",
    "task.unknown",
)


def install_projections(channel_service):
    for kind in KINDS:
        old = channel_service.projections.get(kind)
        if old is not None and old is not project_delegation:
            raise DelegationError("PROJECTION_CONFLICT")
        channel_service.projections[kind] = project_delegation


async def project_delegation(db, stream, event):
    """ChannelService already authenticated authority, serialized cursor and deduped."""
    validate("event", event)
    command, receipt = event["request"], event["receipt"]
    payload, kind = command["payload"], command["kind"]
    key = (stream.authority_node_id, stream.channel_id, payload["delegation_id"])
    if (command["authority_node_id"], command["channel_id"]) != key[:2]:
        raise DelegationError("EVENT_INTEGRITY")
    record = await db.get(DelegationMirror, key, populate_existing=True)
    if kind == "task.request":
        if record is not None or payload["expected_revision"] != 0:
            raise DelegationError("REVISION_CONFLICT")
        source = await db.get(SharedMessage, (*key[:2], payload["source_message_id"]))
        if source is None or source.thread_root_id is not None:
            raise DelegationError("SOURCE_DENIED")
        reserved = await db.scalar(
            select(DelegationMirror.delegation_id).where(
                DelegationMirror.authority_node_id == key[0],
                DelegationMirror.channel_id == key[1],
                DelegationMirror.task_id == payload["task_id"],
                DelegationMirror.state != "rejected",
            )
        )
        if reserved:
            raise DelegationError("CLAIM_CONFLICT")
        state, process, execution_id = "requested", "not_started", None
        record = DelegationMirror(
            authority_node_id=key[0],
            channel_id=key[1],
            delegation_id=key[2],
            task_id=payload["task_id"],
            source_message_id=payload["source_message_id"],
            requester=command["actor"],
            executor=payload["executor"],
        )
        db.add(record)
    else:
        if record is None or payload["expected_revision"] != record.revision:
            raise DelegationError("REVISION_CONFLICT")
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
        if kind not in transitions or record.state not in transitions[kind][0]:
            raise DelegationError("STATE_CONFLICT")
        state, process, execution_id = (
            transitions[kind][1],
            record.process_state,
            record.execution_id,
        )
        if kind != "task.cancel" and command["actor"] != {
            "node_id": record.executor["node_id"],
            "kind": "agent",
            "principal_id": record.executor["agent_id"],
        }:
            raise DelegationError("ACTOR_DENIED")
        if kind == "task.accept":
            execution_id, process = payload["execution_id"], "unknown"
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
            state = "completed" if payload["outcome"] == "succeeded" else "failed"
            process = "finished"
        elif kind == "task.unknown":
            process = "unknown"
        elif kind == "task.cancelled":
            process = payload["process_state"]
            if process == "not_started" and record.process_state == "running":
                raise DelegationError("STATE_CONFLICT")
    revision = payload["expected_revision"] + 1
    if (
        receipt["revision"],
        receipt["state"],
        receipt["process_state"],
        receipt["task_status"],
    ) != (revision, state, process, TASK_STATUS[state]):
        raise DelegationError("EVENT_INTEGRITY")
    record.revision, record.state, record.process_state = revision, state, process
    record.execution_id, record.task_status = execution_id, TASK_STATUS[state]
    await db.flush()
