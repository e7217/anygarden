"""Local admin API and isolated mutually authenticated peer control API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_admin_identity
from anygarden.federation.certificates import CertificateIdentity
from anygarden.federation.errors import PeerError
from anygarden.federation.models import Peer, PeerInvite
from anygarden.federation.schemas import (
    GrantReplace,
    GrantRevokeControl,
    Hello,
    InviteAccept,
    InviteCreate,
    Redeem,
    RevokeControl,
    RotatePin,
)
from anygarden.federation.service import PeerService, now
from anygarden.federation.transport import MAX_BODY, post

router = APIRouter(prefix="/api/v1/node", tags=["node-peers"])
remote_router = APIRouter(prefix="/api/v1/federation", tags=["peer-control"])


def service(request: Request) -> PeerService:
    value = getattr(request.app.state, "peer_service", None)
    if value is None:
        raise PeerError("PEERING_DISABLED", 503)
    return value


def transport_identity(request: Request) -> CertificateIdentity:
    identity = request.scope.get("anygarden.verified_peer")
    if not isinstance(identity, CertificateIdentity):
        raise PeerError("MTLS_REQUIRED", 401)
    return identity


async def error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={"code": exc.code})


async def validation_handler(request, exc):
    # Pydantic's default error contains submitted input, including invite secrets.
    body = getattr(exc, "body", None)
    if (
        request.url.path.startswith("/api/v1/federation/")
        and isinstance(body, dict)
        and body.get("protocol_version", 1) != 1
    ):
        return JSONResponse(status_code=426, content={"code": "VERSION_UNSUPPORTED"})
    return JSONResponse(status_code=400, content={"code": "INVALID_SCHEMA"})


@router.post("/invites", status_code=201)
async def invite(
    body: InviteCreate,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    bundle = await s.create_invite(actor.id, body)
    await s.refresh_trust()
    out = bundle.model_dump(mode="json")
    out["token"] = (
        bundle.token.get_secret_value()
    )  # one intentional delivery to local admin
    return JSONResponse(
        status_code=201, content=out, headers={"Cache-Control": "no-store"}
    )


@router.get("/invites")
async def invites(
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
        rows = (
            await db.execute(
                select(PeerInvite).order_by(PeerInvite.created_at.desc()).limit(100)
            )
        ).scalars()
        return [
            {
                "id": r.id,
                "intended_node_id": r.intended_node_id,
                "state": "expired"
                if r.state == "pending" and r.expires_at <= now()
                else r.state,
                "expires_at": r.expires_at,
            }
            for r in rows
        ]


@router.post("/invites/{invite_id}/accept")
async def accept(
    invite_id: UUID,
    body: InviteAccept,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    if invite_id != body.bundle.invite_id:
        raise PeerError("INVITE_DENIED", 400)
    old = await s.begin_accept(actor.id, body)
    await s.refresh_trust()
    if old:
        return old
    # If this response is lost, the local intent remains pending. Re-submit the
    # SAME invitation; issuer returns its persisted, freshly authorized receipt.
    receipt = await post(
        s,
        body.issuer_endpoint,
        body.bundle.issuer_certificate_pem,
        f"/api/v1/federation/invites/{invite_id}/redeem",
        {
            "protocol_version": 1,
            "sender_node_id": s.node_id,
            "token": body.bundle.token.get_secret_value(),
        },
    )
    return await s.finish_accept(actor.id, body, receipt)


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: UUID,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    await s.revoke_invite(actor.id, str(invite_id))


@router.get("/peers")
async def peers(
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
        rows = (
            await db.execute(select(Peer).order_by(Peer.node_id).limit(100))
        ).scalars()
        return [
            {
                "node_id": r.node_id,
                "fingerprint": r.fingerprint,
                "state": r.state,
                "certificate_epoch": r.epoch,
            }
            for r in rows
        ]


@router.delete("/peers/{node_id}")
async def revoke(
    node_id: UUID,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    return await s.revoke(actor.id, str(node_id))


@router.post("/peers/{node_id}/certificate")
async def rotate(
    node_id: UUID,
    body: RotatePin,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    result = await s.rotate(
        actor.id, str(node_id), body.certificate_pem, body.expected_peer_epoch
    )
    await s.refresh_trust()
    return result


@router.put("/peers/{node_id}/grants/{channel_id}")
async def replace_grant(
    node_id: UUID,
    channel_id: UUID,
    body: GrantReplace,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    return await s.replace_grant(actor.id, str(node_id), str(channel_id), body)


@router.delete("/peers/{node_id}/grants/{channel_id}")
async def revoke_grant(
    node_id: UUID,
    channel_id: UUID,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    return await s.revoke_grant(actor.id, str(node_id), str(channel_id))


@remote_router.post("/hello")
async def hello(
    body: Hello,
    tls: Annotated[CertificateIdentity, Depends(transport_identity)],
    s: Annotated[PeerService, Depends(service)],
):
    return await s.hello(tls, str(body.sender_node_id), body.protocol_version)


@remote_router.post("/invites/{invite_id}/redeem")
async def redeem(
    invite_id: UUID,
    body: Redeem,
    tls: Annotated[CertificateIdentity, Depends(transport_identity)],
    s: Annotated[PeerService, Depends(service)],
):
    return await s.redeem(
        tls, str(body.sender_node_id), str(invite_id), body.token.get_secret_value()
    )


@remote_router.post("/revoke")
async def remote_revoke(
    body: RevokeControl,
    tls: Annotated[CertificateIdentity, Depends(transport_identity)],
    s: Annotated[PeerService, Depends(service)],
):
    return await s.receive_revoke(
        tls, str(body.sender_node_id), str(body.event_id), body.peer_epoch
    )


@remote_router.post("/grants/revoke")
async def remote_grant_revoke(
    body: GrantRevokeControl,
    tls: Annotated[CertificateIdentity, Depends(transport_identity)],
    s: Annotated[PeerService, Depends(service)],
):
    return await s.receive_grant_revoke(
        tls,
        str(body.sender_node_id),
        str(body.event_id),
        body.peer_epoch,
        str(body.channel_id),
        body.grant_epoch,
    )


def mount_admin(app: FastAPI, peer_service: PeerService | None = None):
    """#588 integration: local JWT admin router only. No remote TLS shortcuts."""
    app.state.peer_service = peer_service
    app.include_router(router)
    app.add_exception_handler(PeerError, error_handler)
    # Only this router's validation is replaced; preserve other app contracts.
    previous = app.exception_handlers.get(RequestValidationError)

    async def validation(request, exc):
        if request.url.path.startswith("/api/v1/node/"):
            return await validation_handler(request, exc)
        if previous:
            return await previous(request, exc)
        return await validation_handler(request, exc)

    app.add_exception_handler(RequestValidationError, validation)


def create_peer_app(peer_service: PeerService, channel_service=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.peer_service = peer_service
    app.include_router(remote_router)
    if channel_service is not None:
        from anygarden.shared_channels.router import mount_peer
        mount_peer(app, channel_service)
    app.add_exception_handler(PeerError, error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)

    @app.middleware("http")
    async def bound_body(request, call_next):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                return JSONResponse(status_code=400, content={"code": "INVALID_SCHEMA"})
        request._body = bytes(body)
        return await call_next(request)

    return app
