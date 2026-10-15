"""Local admin API and isolated mutually authenticated peer control API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_admin_identity
from anygarden.federation import connection_codes as codes
from anygarden.federation.certificates import (
    CertificateIdentity,
    certificate_expires_at,
    read_leaf,
)
from anygarden.federation.errors import PeerError
from anygarden.federation.models import Peer, PeerInvite
from anygarden.federation.schemas import (
    ConnectionCardCreate,
    ConnectionCardInspect,
    GrantReplace,
    GrantRevokeControl,
    Hello,
    InviteAccept,
    InviteCodeInspect,
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
        # #842: a known reason (CERTIFICATE_EXPIRED) is more useful than the
        # generic code for the admin who has to fix it.
        reason = getattr(request.app.state, "federation_disabled_reason", None)
        raise PeerError(reason or "PEERING_DISABLED", 503)
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


def public_url(request: Request) -> str | None:
    config = getattr(request.app.state, "config", None)
    return getattr(config, "peer_public_url", None)


async def _dial_preview(url: str) -> dict:
    """Prefill for the admin's explicit endpoint approval; never dials."""
    host, _ = codes.split_peer_url(url)
    ips = await codes.suggest_ips(host)
    return {"suggested_ips": ips, "requires_private": codes.requires_private(ips)}


@router.get("/identity")
async def identity(
    request: Request,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
    # #843: "root" pins renew their TLS leaf automatically; "certificate" is a
    # legacy self-signed pin that needs manual renewal.
    leaf_expires_at = (
        certificate_expires_at(read_leaf(s.cert_path.parent)[0])
        if s.identity.root
        else None
    )
    return {
        "node_id": s.node_id,
        "fingerprint": s.identity.fingerprint,
        "pin_kind": "root" if s.identity.root else "certificate",
        "certificate_expires_at": certificate_expires_at(s.certificate_pem),
        "leaf_expires_at": leaf_expires_at,
        "public_url": public_url(request),
    }


@router.post("/connection-card")
async def connection_card(
    body: ConnectionCardCreate,
    request: Request,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
    url = body.url or public_url(request)
    if not url:
        raise PeerError("PUBLIC_URL_REQUIRED", 400)
    return {
        "card": codes.encode_card(s.node_id, s.certificate_pem, url),
        "node_id": s.node_id,
        "fingerprint": s.identity.fingerprint,
        "url": url,
    }


@router.post("/connection-card/inspect")
async def inspect_connection_card(
    body: ConnectionCardInspect,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
    card, cert = codes.decode_card(body.card)
    if cert.node_id == s.node_id:
        raise PeerError("IDENTITY_MISMATCH", 400)
    return {
        "node_id": cert.node_id,
        "fingerprint": cert.fingerprint,
        "certificate_pem": card.certificate_pem,
        "certificate_expires_at": certificate_expires_at(card.certificate_pem),
        "url": card.url,
        **await _dial_preview(card.url),
    }


@router.post("/invite-code/inspect")
async def inspect_invite_code(
    body: InviteCodeInspect,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    async with s.sessions() as db:
        await s.admin(db, actor.id)
    code, cert = codes.decode_invite(body.code)
    if cert.node_id == s.node_id or str(code.bundle.intended_node_id) != s.node_id:
        raise PeerError("IDENTITY_MISMATCH", 400)
    bundle = code.bundle.model_dump(mode="json")
    # The admin pasted this secret; hand it back once so the UI can accept.
    bundle["token"] = code.bundle.token.get_secret_value()
    return JSONResponse(
        content={
            "bundle": bundle,
            "issuer_node_id": cert.node_id,
            "issuer_fingerprint": cert.fingerprint,
            "issuer_url": code.issuer_url,
            **await _dial_preview(code.issuer_url),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/invites", status_code=201)
async def invite(
    body: InviteCreate,
    request: Request,
    s: Annotated[PeerService, Depends(service)],
    actor: Annotated[Identity, Depends(get_admin_identity)],
):
    issuer_url = body.issuer_url or public_url(request)
    if issuer_url is not None:
        codes.check_peer_url(issuer_url)
    bundle = await s.create_invite(actor.id, body)
    await s.refresh_trust()
    out = bundle.model_dump(mode="json")
    out["token"] = (
        bundle.token.get_secret_value()
    )  # one intentional delivery to local admin
    out["invite_code"] = (
        codes.encode_invite(bundle, issuer_url) if issuer_url is not None else None
    )
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
                "certificate_expires_at": certificate_expires_at(r.certificate_pem),
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
