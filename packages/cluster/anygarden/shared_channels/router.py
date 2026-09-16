"""Opt-in local UI API and isolated mTLS channel transport."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from anygarden.dependencies import get_current_identity, get_admin_identity
from anygarden.federation.router import transport_identity
from anygarden.federation.schemas import Principal
from anygarden.shared_channels.schemas import ChannelError, parse_json
from anygarden.shared_channels.service import ChannelService

local_router = APIRouter(prefix="/api/v1/shared-channels", tags=["shared-channels"])
peer_router = APIRouter(
    prefix="/api/v1/federation/channels", tags=["shared-channel-transport"]
)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadScope(Closed):
    protocol_version: StrictInt = Field(ge=1, le=1)
    sender_node_id: UUID
    authority_node_id: UUID
    channel_id: UUID
    grant_epoch: StrictInt = Field(ge=1)
    actor: Principal


class Replay(ReadScope):
    after_seq: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=50, ge=1, le=100)


class Ack(ReadScope):
    seq: StrictInt = Field(ge=0)


class Binding(Closed):
    authority_node_id: UUID
    channel_id: UUID
    local_room_id: UUID


class Publication(Closed):
    principal: Principal
    active: StrictBool


class ParticipantChange(Publication):
    operation_id: UUID
    expected_revision: StrictInt = Field(ge=0)
    role: str


def service(request: Request) -> ChannelService:
    value = getattr(request.app.state, "channel_service", None)
    if value is None:
        raise ChannelError("SHARING_DISABLED", 503)
    return value


async def read_body(request, model=None):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 65536:
            raise ChannelError("INVALID_SCHEMA", 400)
    body = parse_json(bytes(raw))
    if "protocol_version" in body and (
        type(body["protocol_version"]) is not int or body["protocol_version"] != 1
    ):
        raise ChannelError("VERSION_UNSUPPORTED", 426)
    if model:
        from pydantic import ValidationError

        try:
            return model.model_validate(body).model_dump(mode="json")
        except ValidationError:
            raise ChannelError("INVALID_SCHEMA", 400) from None
    return body


@peer_router.post("/commands")
async def command(
    request: Request, s=Depends(service), tls=Depends(transport_identity)
):
    body = await read_body(request)
    async with s.sessions.begin() as db:
        result = await s.commit_command(db, body, tls=tls)
    return result


@peer_router.post("/replay")
async def replay(request: Request, s=Depends(service), tls=Depends(transport_identity)):
    body = await read_body(request, Replay)
    after, limit = body.pop("after_seq"), body.pop("limit")
    async with s.sessions.begin() as db:
        result = await s.events(db, body, tls=tls, after_seq=after, limit=limit)
    return {"events": result}


@peer_router.post("/ack")
async def acknowledge(
    request: Request, s=Depends(service), tls=Depends(transport_identity)
):
    body = await read_body(request, Ack)
    seq = body.pop("seq")
    async with s.sessions.begin() as db:
        result = await s.ack(db, body, tls=tls, seq=seq)
    return result


@local_router.post("/bindings", status_code=201)
async def binding(
    request: Request, s=Depends(service), actor=Depends(get_admin_identity)
):
    body = await read_body(request, Binding)
    async with s.sessions.begin() as db:
        stream = await s.bind(db, actor_id=actor.id, **body)
        result = {
            "authority_node_id": stream.authority_node_id,
            "channel_id": stream.channel_id,
            "local_room_id": stream.local_room_id,
        }
    return result


@local_router.put("/{channel_id}/publication")
async def publication(
    channel_id: UUID,
    request: Request,
    s=Depends(service),
    actor=Depends(get_admin_identity),
):
    body = await read_body(request, Publication)
    async with s.sessions.begin() as db:
        await s.publication(db, actor_id=actor.id, channel_id=str(channel_id), **body)
    return {"active": body["active"]}


@local_router.post("/{channel_id}/participants")
async def participant(
    channel_id: UUID,
    request: Request,
    s=Depends(service),
    actor=Depends(get_admin_identity),
):
    body = await read_body(request, ParticipantChange)
    async with s.sessions.begin() as db:
        result = await s.change_participant(
            db, actor_id=actor.id, channel_id=str(channel_id), **body
        )
    return result


@local_router.post("/commands")
async def local_command(
    request: Request, s=Depends(service), actor=Depends(get_current_identity)
):
    body = await read_body(request)
    async with s.sessions.begin() as db:
        if body.get("authority_node_id") == s.node_id:
            result = await s.submit_local(db, body, identity=actor)
        else:
            result = await s.queue(db, body, identity=actor)
    return result


@local_router.get("/{authority}/{channel}")
async def snapshot(
    authority: UUID,
    channel: UUID,
    after_seq: int = 0,
    limit: int = 50,
    s=Depends(service),
    actor=Depends(get_current_identity),
):
    if after_seq < 0 or not 1 <= limit <= 100:
        raise ChannelError("INVALID_CURSOR", 400)
    async with s.sessions.begin() as db:
        return await s.snapshot(
            db,
            identity=actor,
            authority=str(authority),
            channel=str(channel),
            after_seq=after_seq,
            limit=limit,
        )


@local_router.post("/{authority}/{channel}/submissions/{request_id}/retry")
async def retry(
    authority: UUID,
    channel: UUID,
    request_id: UUID,
    s=Depends(service),
    actor=Depends(get_current_identity),
):
    from anygarden.shared_channels.sync import retry_submission

    return await retry_submission(
        s, actor, str(authority), str(channel), str(request_id)
    )


@local_router.post("/sync")
async def sync(
    request: Request, s=Depends(service), actor=Depends(get_current_identity)
):
    from anygarden.shared_channels.sync import pull

    body = await read_body(request, ReadScope)
    return await pull(s, actor, body)


async def error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={"code": exc.code})


def mount_local(app, channel_service=None):
    app.state.channel_service = channel_service
    app.include_router(local_router)
    app.add_exception_handler(ChannelError, error_handler)


def mount_peer(app, channel_service):
    app.state.channel_service = channel_service
    app.include_router(peer_router)
    app.add_exception_handler(ChannelError, error_handler)
