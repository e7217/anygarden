"""REST endpoints for system version + PyPI update detection — ``/api/v1/system`` (#546).

- ``GET /version``  — the running server version (any logged-in user).
- ``GET /updates``  — cached update status (admin); never calls out.
- ``POST /check-updates`` — refresh the cache from PyPI (admin).
- ``GET/POST /node-update`` — update the integrated node in place (admin, #927).

The read/write split keeps the UI simple: it only ever reads ``/updates``
(the cache). The SYS-06 background check (``system.update_checker``) fills
the same cache through the same ``check_and_store`` routine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.api.v1.errors import make_api_error
from anygarden.auth.dependencies import Identity
from anygarden.dependencies import forbid_guest, get_admin_identity, get_db
from anygarden.system import update_checker, version_service, version_store

router = APIRouter(prefix="/api/v1/system", tags=["system"])

# Packages the update check tracks (shared with the SYS-06 background check).
_CHECK_PACKAGES = update_checker.CHECK_PACKAGES


class ServerVersion(BaseModel):
    version: str


class PackageUpdate(BaseModel):
    package: str
    current: str
    latest: str | None
    update_available: bool
    checked_at: datetime | None
    error: str | None


def _to_update(
    package: str,
    *,
    latest: str | None,
    checked_at: datetime | None,
    error: str | None,
) -> PackageUpdate:
    current = version_service.get_local_version(package)
    return PackageUpdate(
        package=package,
        current=current,
        latest=latest,
        update_available=version_service.is_update_available(current, latest),
        checked_at=checked_at,
        error=error,
    )


async def _read_updates(db: AsyncSession) -> list[PackageUpdate]:
    rows = {r.package: r for r in await version_store.get_all(db)}
    result: list[PackageUpdate] = []
    for package in _CHECK_PACKAGES:
        row = rows.get(package)
        result.append(
            _to_update(
                package,
                latest=row.latest_version if row else None,
                checked_at=row.checked_at if row else None,
                error=row.error if row else None,
            )
        )
    return result


@router.get("/version", response_model=ServerVersion)
async def get_version(_: Identity = Depends(forbid_guest)) -> ServerVersion:
    """Return the running server version (all logged-in users)."""
    return ServerVersion(version=version_service.get_local_version("anygarden"))


@router.get("/updates", response_model=list[PackageUpdate])
async def get_updates(
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[PackageUpdate]:
    """Return the cached update status per package (no outbound call)."""
    return await _read_updates(db)


@router.post("/check-updates", response_model=list[PackageUpdate])
async def check_updates(
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> list[PackageUpdate]:
    """Query PyPI for each tracked package, refresh the cache, and return it."""
    await update_checker.check_and_store(db)
    await db.commit()
    return await _read_updates(db)


# ── Integrated node self-update (#927) ───────────────────────────────


class NodeUpdateStatus(BaseModel):
    # False with a ``reason`` when this process cannot update itself.
    supported: bool
    reason: str | None
    # idle | installing | restarting | failed (in memory; idle after re-exec).
    status: str
    from_version: str
    target_version: str | None
    error: str | None


class NodeUpdateTrigger(BaseModel):
    # Defaults to the latest release the update check cached.
    target_version: str | None = None


def _is_integrated_node(app) -> bool:
    return (
        app.state.config.local_node_data_dir is not None
        and getattr(app.state, "node_shutdown_callback", None) is not None
    )


async def _node_update_reason(app) -> str | None:
    # Detection may shell out to ``uv tool dir``; compute once per process.
    if not hasattr(app.state, "node_update_reason"):
        from anygarden.node.self_update import unsupported_reason

        integrated = _is_integrated_node(app)
        app.state.node_update_reason = await asyncio.to_thread(
            unsupported_reason, integrated=integrated
        )
    return app.state.node_update_reason


def _node_updater(app):
    updater = getattr(app.state, "node_updater", None)
    if updater is None:
        from anygarden.node.self_update import NodeUpdater

        def request_restart() -> None:
            # ``start_node`` re-executes after a confirmed clean shutdown.
            app.state.node_restart_requested = True
            app.state.node_shutdown_callback()

        updater = NodeUpdater(
            current_version=version_service.get_local_version("anygarden"),
            request_restart=request_restart,
        )
        app.state.node_updater = updater
    return updater


async def _node_update_status(app) -> NodeUpdateStatus:
    reason = await _node_update_reason(app)
    updater = getattr(app.state, "node_updater", None)
    snap = (
        updater.snapshot()
        if updater is not None
        else {
            "status": "idle",
            "from_version": version_service.get_local_version("anygarden"),
            "target_version": None,
            "error": None,
        }
    )
    return NodeUpdateStatus(supported=reason is None, reason=reason, **snap)


@router.get("/node-update", response_model=NodeUpdateStatus)
async def get_node_update(
    request: Request,
    _: Identity = Depends(get_admin_identity),
) -> NodeUpdateStatus:
    """Whether this node can update itself, and how the current attempt is going."""
    return await _node_update_status(request.app)


@router.post(
    "/node-update",
    response_model=NodeUpdateStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_node_update(
    request: Request,
    body: NodeUpdateTrigger | None = None,
    _: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> NodeUpdateStatus:
    """Install a newer release, then restart this node in place (#927).

    Returns once the install has started; poll ``GET /node-update``. The
    node goes offline while it restarts and comes back on the new version.
    """
    from anygarden.node.self_update import NodeUpdateBusy, NodeUpdateRejected

    app = request.app
    reason = await _node_update_reason(app)
    if reason is not None:
        raise make_api_error(
            status_code=409,
            code="NODE_UPDATE_UNAVAILABLE",
            message=f"This node cannot update itself ({reason})",
            reason=reason,
        )
    target = body.target_version if body else None
    if target is None:
        rows = {r.package: r for r in await version_store.get_all(db)}
        cached = rows.get("anygarden")
        target = cached.latest_version if cached else None
    if target is None:
        raise make_api_error(
            status_code=409,
            code="NODE_UPDATE_NO_TARGET",
            message="No newer release is known; check for updates first",
        )
    try:
        _node_updater(app).start(target)
    except NodeUpdateBusy as exc:
        raise make_api_error(
            status_code=409, code="NODE_UPDATE_BUSY", message=str(exc)
        ) from exc
    except NodeUpdateRejected as exc:
        raise make_api_error(
            status_code=409, code="NODE_UPDATE_REJECTED", message=str(exc)
        ) from exc
    return await _node_update_status(app)
