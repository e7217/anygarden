"""Admin API: this node also serves other servers as one of their machines (#848).

Only an integrated node (``anygarden start``) has machine links. The code
carries a machine token, so it is parsed by hand and never echoed back.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_admin_identity

router = APIRouter(prefix="/api/v1/machine-links", tags=["machine-links"])

_MAX_BODY = 8192


def _fail(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _manager(request: Request):
    manager = getattr(request.app.state, "machine_links", None)
    if manager is None:
        raise _fail(
            "MACHINE_LINKS_UNAVAILABLE",
            "Only a node started with `anygarden start` can serve other servers.",
            404,
        )
    return manager


async def _code(request: Request) -> str:
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise _fail("MACHINE_LINK_CODE_INVALID", "The code is too long.", 422)
    try:
        body = json.loads(raw)
        code = body["code"]
        if not isinstance(code, str):
            raise TypeError
        return code
    except (ValueError, TypeError, KeyError):
        raise _fail(
            "MACHINE_LINK_CODE_INVALID", "Send the connection code as {\"code\": ...}.", 422
        ) from None


def _error(exc):
    return _fail(exc.code, str(exc), exc.status)


@router.get("")
async def list_machine_links(
    request: Request, _: Annotated[Identity, Depends(get_admin_identity)]
) -> list[dict]:
    return _manager(request).list()


@router.post("", status_code=201)
async def add_machine_link(
    request: Request, _: Annotated[Identity, Depends(get_admin_identity)]
) -> dict:
    from anygarden.node.machine_links import MachineLinkError

    manager = _manager(request)
    code = await _code(request)
    try:
        return await manager.add(code)
    except MachineLinkError as exc:
        raise _error(exc) from None


@router.delete("/{machine_id}", status_code=204)
async def remove_machine_link(
    machine_id: str,
    request: Request,
    _: Annotated[Identity, Depends(get_admin_identity)],
) -> None:
    from anygarden.node.machine_links import MachineLinkError

    try:
        await _manager(request).remove(machine_id)
    except MachineLinkError as exc:
        raise _error(exc) from None
