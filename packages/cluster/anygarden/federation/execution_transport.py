"""Correlated control over the assigned agent's authenticated canonical DM.

No agent/runtime dependency: this module also runs in server-only deployments.
Authority/grant admission belongs to the coordinator; each request additionally
fences the current local agent placement, generation and live socket instance.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from anygarden.db.models import Agent, Participant, Room
from anygarden.ws.protocol import ExecutionControlOut

ACTIONS = frozenset({"prepare", "describe", "start", "reconcile", "cancel", "revoke"})


class ExecutionTransportError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class _Pending:
    agent_id: str
    participant_id: str
    room_id: str
    generation: int
    socket_epoch: str
    action: str
    future: asyncio.Future


class ExecutionTransport:
    def __init__(
        self,
        sessions,
        connection_manager,
        *,
        timeout_seconds: float = 5,
        pending_limit: int = 32,
    ):
        self.sessions = sessions
        self.manager = connection_manager
        self.timeout_seconds = timeout_seconds
        self.pending_limit = pending_limit
        self._pending: dict[str, _Pending] = {}
        self._closed = False
        connection_manager.execution_transport = self

    async def _target(self, agent_id: str):
        async with self.sessions() as db:
            agent = await db.get(Agent, agent_id)
            if agent is None or agent.placed_on_machine_id is None:
                raise ExecutionTransportError("EXECUTION_UNAVAILABLE")
            candidates = (
                await db.execute(
                    select(Participant.id, Room.id)
                    .join(
                        Room,
                        Participant.room_id == Room.id,
                    )
                    .where(
                        Participant.agent_id == agent_id,
                        Participant.role.in_(("member", "admin", "owner")),
                        Room.is_dm.is_(True),
                        Room.representative_agent_id == agent_id,
                        Room.archived_at.is_(None),
                    )
                )
            ).all()
            if len(candidates) != 1:
                raise ExecutionTransportError("EXECUTION_UNAVAILABLE")
            participant_id, room_id = candidates[0]
            return participant_id, room_id, agent.generation, agent.placed_on_machine_id

    async def request(
        self,
        agent_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[str, Any]:
        if self._closed or action not in ACTIONS:
            raise ExecutionTransportError("EXECUTION_UNAVAILABLE")
        if len(self._pending) >= self.pending_limit:
            raise ExecutionTransportError("EXECUTION_QUEUE_FULL")
        participant_id, room_id, generation, machine_id = await self._target(agent_id)
        if expected_generation is not None and generation != expected_generation:
            raise ExecutionTransportError("GENERATION_CHANGED")
        connection = await self.manager.execution_connection(participant_id)
        if connection is None or connection[0] != generation:
            raise ExecutionTransportError("EXECUTION_UNAVAILABLE")
        if len(self._pending) >= self.pending_limit:
            raise ExecutionTransportError("EXECUTION_QUEUE_FULL")
        socket_epoch = connection[1]
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = _Pending(
            agent_id,
            participant_id,
            room_id,
            generation,
            socket_epoch,
            action,
            future,
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                sent = await self.manager.send_to(
                    participant_id,
                    ExecutionControlOut(
                        request_id=request_id,
                        agent_id=agent_id,
                        generation=generation,
                        action=action,
                        payload=payload,
                    ),
                    expected_generation=generation,
                    expected_socket_epoch=socket_epoch,
                )
                if not sent:
                    raise ExecutionTransportError("EXECUTION_UNAVAILABLE")
                result = await future
            # A response can race a placement/config transaction. Revalidate
            # outside the WS handler as well before giving the caller a DTO.
            target = await self._target(agent_id)
            if target != (participant_id, room_id, generation, machine_id):
                raise ExecutionTransportError("GENERATION_CHANGED")
            if "error_code" in result:
                raise ExecutionTransportError(result["error_code"])
            return result["result"]
        except TimeoutError as exc:
            raise ExecutionTransportError("EXECUTION_TIMEOUT") from exc
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Consume a disconnect racing a failed send.

    async def resolve(
        self, *, agent_id: str, participant_id: str, room_id: str, websocket, frame
    ) -> bool:
        pending = self._pending.get(frame.request_id)
        if (
            pending is None
            or pending.future.done()
            or (
                pending.agent_id != agent_id
                or pending.participant_id != participant_id
                or pending.room_id != room_id
                or pending.generation != frame.generation
                or pending.action != frame.action
            )
        ):
            return False
        connection = await self.manager.execution_connection(
            participant_id, websocket=websocket
        )
        if connection != (pending.generation, pending.socket_epoch):
            return False
        if frame.error_code is not None:
            pending.future.set_result({"error_code": frame.error_code})
        elif isinstance(frame.result, dict):
            pending.future.set_result({"result": frame.result})
        else:
            return False
        return True

    def disconnected(self, participant_id: str, socket_epoch: str) -> None:
        for pending in self._pending.values():
            if (
                pending.participant_id == participant_id
                and pending.socket_epoch == socket_epoch
                and not pending.future.done()
            ):
                pending.future.set_exception(
                    ExecutionTransportError("CONTROL_DISCONNECTED")
                )

    async def close(self) -> None:
        self._closed = True
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(
                    ExecutionTransportError("CONTROL_DISCONNECTED")
                )
        if getattr(self.manager, "execution_transport", None) is self:
            self.manager.execution_transport = None
