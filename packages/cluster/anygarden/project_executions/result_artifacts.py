"""Freeze canonical publications into a result without relying on copied fields."""

from __future__ import annotations

from sqlalchemy import select

from anygarden.db.models import ProjectExecutionEvent
from anygarden.project_executions.service import ExecutionConflict

REFERENCE_FIELDS = frozenset({
    "artifact_id", "room_id", "filename", "sha256", "producer_agent_id",
    "task_id", "execution_id", "input_revision", "url",
})


def publication_producer(*, agent_id: str, proof, attempt_id: str) -> dict:
    """Public invocation identity; a lease or native handle is never recorded."""
    return {
        "agent_id": agent_id, "request_id": proof.request_id,
        "attempt_id": attempt_id, "attempt_number": proof.attempt,
        "generation": proof.generation,
    }


def _supplied_references(artifacts: list | None, canonical: list[dict]) -> list[dict]:
    if artifacts is not None and not isinstance(artifacts, list):
        raise ExecutionConflict("ARTIFACT_REFERENCE_INVALID", "Artifact references must be an array")
    completed = []
    for supplied in artifacts or []:
        if (not isinstance(supplied, dict) or not isinstance(supplied.get("artifact_id"), str)
            or not supplied["artifact_id"] or set(supplied) - REFERENCE_FIELDS):
            raise ExecutionConflict(
                "ARTIFACT_REFERENCE_INVALID",
                "Use a published artifact_id and only canonical artifact reference fields",
            )
        candidates = [item for item in canonical if item.get("artifact_id") == supplied["artifact_id"]]
        matches = [item for item in candidates if all(
            name in item and type(value) is type(item[name]) and value == item[name]
            for name, value in supplied.items()
        )]
        if not matches:
            raise ExecutionConflict(
                "ARTIFACT_PROVENANCE_INVALID",
                "The supplied artifact ID or declared metadata does not match this task's publication",
            )
        # An immutable publication supplies missing room/hash/producer/link
        # fields. Supplied values are never silently corrected.
        completed.append(dict(matches[0]))
    return completed


def replay_result_artifacts(*, artifacts: list | None, frozen: list) -> list[dict]:
    """Replay only the accepted reference set, never a later publication set."""
    if artifacts is None or artifacts == []:
        return list(frozen)
    return _supplied_references(artifacts, frozen)


async def resolve_result_artifacts(
    db, *, execution, task, agent_id: str, proof, attempt_id: str,
    artifacts: list | None,
) -> list[dict]:
    """Auto-bind only this invocation's publications; explicit refs stay scoped.

    Explicit references retain compatibility with a task's previously published
    outputs. Automatic association requires producer metadata matching the
    exact current attempt and never adopts legacy or another attempt's partial
    output. The caller still validates DB artifact scope/hash before accepting.
    """
    if artifacts is not None and not isinstance(artifacts, list):
        raise ExecutionConflict("ARTIFACT_REFERENCE_INVALID", "Artifact references must be an array")
    expected = publication_producer(agent_id=agent_id, proof=proof, attempt_id=attempt_id)
    events = await db.scalars(select(ProjectExecutionEvent).where(
        ProjectExecutionEvent.execution_id == execution.id,
        ProjectExecutionEvent.task_id == task.id,
        ProjectExecutionEvent.event_type == "artifact_published",
    ).order_by(ProjectExecutionEvent.created_at, ProjectExecutionEvent.id))
    canonical = []
    automatic = []
    seen = set()
    for event in events:
        details = event.details if isinstance(event.details, dict) else {}
        references = details.get("artifacts")
        if not isinstance(references, list):
            continue
        for reference in references:
            if (not isinstance(reference, dict) or set(reference) != REFERENCE_FIELDS
                or reference.get("task_id") != task.id
                or reference.get("execution_id") != execution.id
                or type(reference.get("input_revision")) is not int
                or reference["input_revision"] != task.input_revision):
                continue
            canonical.append(reference)
            if details.get("producer") == expected and reference["artifact_id"] not in seen:
                automatic.append(dict(reference))
                seen.add(reference["artifact_id"])
    return _supplied_references(artifacts, canonical) if artifacts else automatic
