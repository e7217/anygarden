"""Lease-authenticated project execution tools for local room agents."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy import select

from anygarden.project_executions.policy import SUPPORTED_ACTIONS, SUPPORTED_LIMITS


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name, "description": description,
        "inputSchema": {
            "type": "object", "properties": properties, "required": required,
            "additionalProperties": False,
        },
    }


_string = {"type": "string", "minLength": 1}

PROJECT_TOOL_SCHEMAS = [
    _tool(
        "begin_project_execution",
        "Begin one project execution for the human request you are currently answering "
        "in the operating room. Reuses this turn, links the original request and snapshots "
        "its supplied files. Returns root task, execution and eligible subroom assignees. "
        "Use this before delegating work to subrooms; never simulate delegation in text.",
        {
            "objective": _string,
            "completion_criteria": {"type": "array", "items": _string},
            "allowed_actions": {"type": "array", "items": {
                **_string, "enum": sorted(SUPPORTED_ACTIONS),
            }, "description": "Use internal_work for internal workflow. Managed submission "
               "or deployment additionally requires managed_submission or managed_deployment. "
               "These permissions govern server tools; they do not enforce native tool egress."},
            "limits": {"type": "object", "properties": {
                "max_delegations": {"type": "integer", "minimum": 1},
                "max_depth": {"type": "integer", "minimum": 1},
                "deadline_at": {"type": "string", "format": "date-time"},
                "max_repair_rounds": {"type": "integer", "minimum": 0, "default": 0,
                    "description": "Explicit execution-wide automatic QA repair budget. "
                                   "Each round reserves a same-task repair and independent re-QA "
                                   "within max_delegations. Omitted or zero means no automatic repair."},
                "max_native_invocations": {"type": "integer", "minimum": 1,
                    "description": "Execution-wide unique native start permits, including the "
                                   "initial lead, retries and all revisions. This does not count "
                                   "provider-internal requests within one native invocation."},
                "max_total_tokens": {"type": "integer", "minimum": 1,
                    "description": "Stop further managed work when authenticated terminal "
                                   "input+output token deltas reach this execution-wide threshold. "
                                   "Pending/unknown usage is explicit; in-flight invocations may "
                                   "overshoot. This is not a per-provider-request hard spending cap."},
            }, "additionalProperties": False, "description":
               "Supported server limits: " + ", ".join(sorted(SUPPORTED_LIMITS))},
            "requires_qa": {"type": "boolean"},
        }, [],
    ),
    _tool(
        "delegate_project_task",
        "Create and actually dispatch one task in a descendant subroom of this execution. "
        "Include goal, constraints, expected output and completion criteria in spec. "
        "Snapshot inputs are passed automatically. Use depends_on to wait for successful "
        "prerequisite results. Reuse a stable delegation_key on retry. Assignee IDs come "
        "from begin_project_execution, not room IDs. Plan independent review as role=qa "
        "with qa_target_task_id. You may omit qa_target_result_version while the target "
        "is unfinished: QA waits for its prerequisites and the server freezes the exact "
        "accepted version before dispatch. If a version is supplied it must be current. "
        "Do not create a preliminary work-role review merely to wait for target results.",
        {
            "execution_id": _string, "parent_task_id": _string,
            "target_room_id": _string, "assignee_participant_id": _string,
            "delegation_key": _string, "title": {**_string, "maxLength": 500},
            "spec": {**_string, "maxLength": 100000},
            "role": {"type": "string", "default": "work", "enum": [
                "work", "qa", "repair", "planning", "research", "implementation", "release",
            ]},
            "required": {"type": "boolean", "default": True},
            "depends_on": {"type": "array", "items": _string},
            "qa_target_task_id": _string,
            "qa_target_result_version": {"type": "integer", "minimum": 1,
                "description": "Optional for a planned QA target: the current accepted "
                               "version is bound before the review starts."},
        }, ["execution_id", "parent_task_id", "target_room_id",
            "assignee_participant_id", "delegation_key", "title", "spec"],
    ),
    _tool(
        "seal_project_plan",
        "Seal the required task manifest after delegating every required stage. "
        "The execution cannot complete until all these tasks succeed with recorded "
        "results and any independent QA checks accept the current output versions. "
        "Do not omit unfinished work to make a plan appear complete. When required "
        "tasks are still waiting, the server resumes the lead automatically after results "
        "arrive: end this turn with a brief status instead of polling or waiting. "
        "Mark the root done only after all required outputs and QA are verified.",
        {"execution_id": _string, "required_task_ids": {
            "type": "array", "items": _string, "minItems": 1,
        }}, ["execution_id", "required_task_ids"],
    ),
    _tool(
        "get_project_execution",
        "Read the execution bound to your current leased task. The lead sees the "
        "whole task/result/event ledger; a worker sees only its own task and accepted "
        "prerequisite results, exact numeric versions, artifact references and own approvals. "
        "Use this on resumption; do not ask the user for server-owned result metadata "
        "or begin another execution. Artifact references and result summaries are not "
        "the document body: use read_project_artifact to inspect actual published text. "
        "If required work is waiting, end this turn with a brief status; the server "
        "resumes the lead automatically. Do not busy-poll or mark the root done early.",
        {"execution_id": _string}, ["execution_id"],
    ),
    _tool(
        "request_project_input",
        "Ask the user for missing facts needed by your current delegated task. "
        "The question is posted in the operating room and linked to this task and "
        "input revision. Only this task waits; user answers resume it automatically. "
        "Use a stable question_key, then end your turn without marking done.",
        {
            "task_id": _string, "question_key": _string,
            "question": {**_string, "maxLength": 20000},
        }, ["task_id", "question_key", "question"],
    ),
    _tool(
        "publish_project_artifact",
        "Write an actual text artifact for your current task. It becomes available "
        "to download in the originating operating room and your work room. "
        "Returns real artifact IDs, hash and URL. The server automatically connects "
        "this invocation's publications when mark_task_status.artifacts is omitted. "
        "If choosing specific files, use their artifact_id; canonical metadata is "
        "supplied by the server and any metadata you declare must match exactly. "
        "Use the returned URLs for document links instead of reconstructing IDs. "
        "Publish the full document here; local file paths are not server downloads. "
        "Review published bodies with read_project_artifact. Never invent file links.",
        {
            "task_id": _string, "filename": _string,
            "content": {**_string, "maxLength": 250000},
            "mime": {"type": "string", "default": "text/markdown"},
        }, ["task_id", "filename", "content"],
    ),
    _tool(
        "read_project_artifact",
        "Read the actual UTF-8 document body of a published artifact, not its result "
        "summary. Use real artifact IDs returned by publish_project_artifact or "
        "get_project_execution. You may read your current task's publications and "
        "exact accepted prerequisite artifacts; the current root lead may review this "
        "execution input revision's publications. The server verifies publication scope "
        "and the stored bytes' SHA-256. Read all chunks until next_offset is null before "
        "claiming you inspected the whole document. Confirm each response contains "
        "the full contiguous requested chunk. If output is truncated, ignore its "
        "next_offset and reread from that request's starting offset with a smaller "
        "limit until the complete chunk is received. Never count a truncated response "
        "as a completed read. Offsets and limits count Unicode "
        "characters, not bytes. Never supply a filesystem path.",
        {
            "task_id": _string, "artifact_id": _string,
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 12000, "default": 3000},
        }, ["task_id", "artifact_id"],
    ),
    _tool(
        "request_project_approval",
        "Request explicit human approval in the operating room for one managed external "
        "action. Use only a configured target alias returned by begin/get_project_execution. "
        "The server freezes the exact accepted source task result version and artifact hash. "
        "Only your action task blocks; end your turn without marking done. Free-text assent "
        "is not approval. On approval a new turn resumes this task with the approval ID. "
        "Requires managed_submission or managed_deployment in the execution permissions.",
        {
            "task_id": _string, "action_key": {**_string, "maxLength": 160},
            "action_kind": {"type": "string", "enum": ["submission", "deployment"]},
            "target_alias": {**_string, "maxLength": 160},
            "source_task_id": _string,
            "source_result_version": {"type": "integer", "minimum": 1},
            "artifact_id": _string, "summary": {**_string, "maxLength": 20000},
        }, ["task_id", "action_key", "action_kind", "target_alias", "source_task_id",
            "source_result_version", "artifact_id", "summary"],
    ),
    _tool(
        "execute_approved_project_action",
        "Execute only the exact managed action frozen by this approval. The server consumes "
        "the current task's approved permit once and sends the approved artifact to the "
        "configured target. Repeated calls do not resend. An ambiguous outcome is unknown "
        "and requires investigation, never a new automatic submission. Report the actual "
        "receipt and finish your task only after status succeeded. The submitted file remains "
        "the source task's artifact: cite its URL in result_markdown, but do not claim it as "
        "your own mark_task_status.artifacts. Use an empty artifacts list unless your action "
        "task publishes a new receipt document.",
        {"approval_id": _string}, ["approval_id"],
    ),
    _tool(
        "complete_project_execution",
        "Finish the whole execution and publish its final operating-room report once. "
        "The server checks the sealed required work and recorded results/QA. Include "
        "result, artifact links, verification evidence, limitations and next actions. "
        "A pending, blocked, failed or missing task prevents completion.",
        {"execution_id": _string, "summary": {**_string, "maxLength": 100000}},
        ["execution_id", "summary"],
    ),
]
PROJECT_TOOL_NAMES = frozenset(tool["name"] for tool in PROJECT_TOOL_SCHEMAS)


def turn_proof(request: Request):
    from anygarden.project_executions.service import TurnProof

    headers = request.headers
    try:
        return TurnProof(
            request_id=headers["x-anygarden-turn-request-id"],
            attempt=int(headers["x-anygarden-turn-attempt"]),
            generation=int(headers["x-anygarden-turn-generation"]),
            lease=headers["x-anygarden-turn-lease"],
        )
    except (KeyError, ValueError):
        raise HTTPException(409, "Execution tools require the current delivered turn lease") from None


async def _input_files(db, room_id: str, root: Path) -> list[dict]:
    from anygarden.db.models import RoomSharedFile
    inputs = []
    for file in await db.scalars(select(RoomSharedFile).where(RoomSharedFile.room_id == room_id)):
        try:
            raw = (root / file.storage_path).read_bytes()
            content = raw.decode("utf-8")
        except (FileNotFoundError, UnicodeError, OSError):
            raise HTTPException(409, f"Input file is unavailable: {file.filename}") from None
        if sha256(raw).hexdigest() != file.sha256:
            raise HTTPException(409, f"Input file changed while being read: {file.filename}")
        inputs.append({
            "file_id": file.id, "room_id": room_id, "filename": file.filename,
            "sha256": file.sha256, "content": content,
        })
    return inputs


async def _eligible_rooms(db, execution) -> list[dict]:
    from anygarden.db.models import Agent, Participant, Room

    all_rooms = list(await db.scalars(select(Room).where(
        Room.project_id == execution.project_id, Room.archived_at.is_(None),
    )))
    descendants = {execution.operating_room_id}
    while True:
        expanded = descendants | {r.id for r in all_rooms if r.parent_room_id in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    rooms = []
    for room in all_rooms:
        if room.id not in descendants or room.id == execution.operating_room_id:
            continue
        assignees = await db.execute(select(Participant, Agent).join(
            Agent, Agent.id == Participant.agent_id,
        ).where(Participant.room_id == room.id, Agent.id != execution.lead_agent_id))
        rooms.append({
            "room_id": room.id, "name": room.name,
            "assignees": [{"participant_id": p.id, "agent_id": a.id, "name": a.name}
                          for p, a in assignees],
        })
    return rooms


def execution_payload(execution) -> dict:
    return {
        "execution_id": execution.id, "root_task_id": execution.root_task_id,
        "operating_room_id": execution.operating_room_id,
        "source_message_id": execution.source_message_id,
        "input_revision": execution.input_revision, "status": execution.status,
        "objective": execution.objective,
    }


def action_target_payload(config) -> list[dict]:
    return [{"alias": alias, "label": target["label"], "action_kind": target["action_kind"],
             "url": target["url"]}
            for alias, target in config.project_action_targets.items()]


async def call_project_tool(request: Request, *, agent_id: str, name: str, arguments: dict) -> dict:
    from anygarden.project_executions import service

    db = None
    try:
        proof = turn_proof(request)
        if name == "execute_approved_project_action":
            from anygarden.project_executions.action_executor import (
                execute_approved_action,
            )
            from anygarden.project_executions.approvals import approval_payload

            async def on_transition(approval_id: str, task_id: str) -> None:
                from anygarden.db.execution_approval_models import ExecutionApproval
                from anygarden.db.models import Message, Room, Task
                from anygarden.messages.service import fanout_task_event

                async with request.app.state.session_factory() as event_db:
                    task = await event_db.get(Task, task_id)
                    if task is not None:
                        room = await event_db.get(Room, task.room_id)
                        await fanout_task_event(
                            event_db, manager=getattr(request.app.state, "connection_manager", None),
                            event="updated", task=task, room_name=room.name if room else "",
                        )
                    approval = await event_db.get(ExecutionApproval, approval_id)
                    if approval and approval.result_message_id:
                        message = await event_db.get(Message, approval.result_message_id)
                        if message is not None:
                            await broadcast_project_messages(event_db, request=request, messages=[message])

            item = await execute_approved_action(
                request.app.state.session_factory, agent_id=agent_id, proof=proof,
                targets=request.app.state.config.project_action_targets,
                artifact_files_dir=Path(request.app.state.config.artifact_files_dir),
                on_transition=on_transition, **arguments,
            )
            payload = approval_payload(item)
            return {"content": [{"type": "text", "text": str(payload)}],
                    "structuredContent": payload, "isError": False}
        async with request.app.state.session_factory() as db:
            turn = await service.authorize_turn(db, agent_id=agent_id, proof=proof)
            if name == "begin_project_execution":
                inputs = await _input_files(db, turn.room_id, Path(request.app.state.config.room_files_dir))
                execution = await service.begin_execution(
                    db, agent_id=agent_id, proof=proof, input_files=inputs, **arguments,
                )
                payload = {**execution_payload(execution), "subrooms": await _eligible_rooms(db, execution),
                           "action_targets": action_target_payload(request.app.state.config)}
            elif name == "delegate_project_task":
                task = await service.delegate_task(db, agent_id=agent_id, proof=proof, **arguments)
                payload = {
                    "execution_id": task.execution_id, "task_id": task.id,
                    "room_id": task.room_id, "assignee_participant_id": task.assignee_participant_id,
                    "status": task.status, "input_revision": task.input_revision,
                }
            elif name == "seal_project_plan":
                execution = await service.seal_plan(db, agent_id=agent_id, proof=proof, **arguments)
                payload = execution_payload(execution)
            elif name == "get_project_execution":
                from anygarden.db.models import ProjectExecution, Task

                execution = await db.get(ProjectExecution, arguments["execution_id"])
                bound_task = await db.get(Task, turn.task_id) if turn.task_id else None
                if (execution is None or bound_task is None or bound_task.execution_id != execution.id
                    or bound_task.assignee_participant_id != turn.target_participant_id
                    or bound_task.room_id != turn.room_id):
                    raise HTTPException(403, "This execution is not bound to your current task turn")
                if execution.root_task_id == turn.task_id and execution.lead_agent_id == agent_id:
                    payload = await service.get_execution_detail(db, execution.id)
                    payload["subrooms"] = await _eligible_rooms(db, execution)
                else:
                    payload = await service.get_bound_task_execution_detail(
                        db, execution_id=execution.id, task_id=bound_task.id,
                    )
                payload["action_targets"] = action_target_payload(request.app.state.config)
            elif name == "request_project_input":
                from anygarden.project_executions.requests import (
                    question,
                    request_payload,
                )

                item = await question(db, agent_id=agent_id, proof=proof, **arguments)
                payload = request_payload(item)
            elif name == "request_project_approval":
                from anygarden.project_executions.approvals import (
                    approval_payload,
                    request_approval,
                )

                item = await request_approval(
                    db, agent_id=agent_id, proof=proof,
                    targets=request.app.state.config.project_action_targets,
                    artifact_files_dir=Path(request.app.state.config.artifact_files_dir),
                    **arguments,
                )
                payload = approval_payload(item)
            elif name == "publish_project_artifact":
                from anygarden.project_executions.artifacts import publish_artifact

                payload = await publish_artifact(
                    db, agent_id=agent_id, proof=proof,
                    artifact_files_dir=Path(request.app.state.config.artifact_files_dir),
                    **arguments,
                )
            elif name == "read_project_artifact":
                from anygarden.project_executions.artifact_reader import (
                    read_project_artifact,
                )

                payload = await read_project_artifact(
                    db, agent_id=agent_id, proof=proof,
                    artifact_files_dir=Path(request.app.state.config.artifact_files_dir),
                    **arguments,
                )
            elif name == "complete_project_execution":
                execution = await service.finish_execution(db, agent_id=agent_id, proof=proof, **arguments)
                payload = execution_payload(execution)
            else:
                raise HTTPException(400, "Unknown execution tool")
            messages = list(db.info.pop("project_execution_messages", []))
            usage_denials = dict(db.info.pop("project_execution_usage_denials", {}))
            await db.commit()
            if usage_denials:
                from anygarden.project_executions.limits import (
                    apply_queued_usage_denials,
                )

                await apply_queued_usage_denials(request.app, denials=usage_denials)
            await broadcast_project_messages(db, request=request, messages=messages)
            from anygarden.db.models import Room, Task
            from anygarden.messages.service import fanout_task_event

            task_id = payload.get("task_id") or payload.get("root_task_id")
            task = await db.get(Task, task_id) if task_id else None
            if task is not None and name != "read_project_artifact":
                room = await db.get(Room, task.room_id)
                await fanout_task_event(
                    db, manager=getattr(request.app.state, "connection_manager", None),
                    event="updated", task=task, room_name=room.name if room else "",
                )
        return {"content": [{"type": "text", "text": str(payload)}], "structuredContent": payload, "isError": False}
    except HTTPException as exc:
        return {"content": [{"type": "text", "text": str(exc.detail)}], "isError": True}
    except (TypeError, ValueError) as exc:
        if isinstance(exc, service.ExecutionConflict) and db is not None:
            # The failed tool session has left its context and rolled back.
            # Revalidate the closed denial in a fresh transaction so the limit
            # report/stop is durable even though the requested work was denied.
            from anygarden.project_executions.limits import apply_queued_usage_denials

            await apply_queued_usage_denials(request.app,
                denials=dict(db.info.pop("project_execution_usage_denials", {})))
        if name == "read_project_artifact" and isinstance(exc, service.ExecutionConflict):
            return {"content": [{"type": "text", "text": f"{exc.code}: {exc.detail}"}],
                    "structuredContent": {"code": exc.code, "detail": exc.detail}, "isError": True}
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}


async def broadcast_project_messages(db, *, messages: list, request: Request | None = None, app=None) -> None:
    """Publish committed projections; durable assignees receive only their lease."""
    from anygarden.db.models import AgentTurn
    from anygarden.messages.serialization import message_to_frame

    application = request.app if request is not None else app
    manager = getattr(application.state, "connection_manager", None)
    if manager is None:
        return
    for message in messages:
        target = await db.scalar(select(AgentTurn.target_participant_id).where(
            AgentTurn.trigger_message_id == message.id,
        ))
        frame = message_to_frame(message)
        await manager.broadcast_tailored(
            message.room_id,
            lambda pid, frame=frame, target=target: None if target and pid == target else frame,
        )
