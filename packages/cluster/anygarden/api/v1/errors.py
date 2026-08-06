"""Shared REST API error helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def make_api_error(
    code: str,
    message: str,
    status_code: int,
    *,
    legacy_error: str | None = None,
    **extra: Any,
) -> HTTPException:
    """Build normalized error payloads for REST endpoints.

    The public contract prefers ``code`` + ``message`` (plus ``detail``
    for compatibility with existing FastAPI-style payload parsing),
    with optional ``error`` compatibility fields where legacy clients still
    inspect ``detail.error``.
    """
    detail = {"code": code, "message": message, "detail": message, **extra}
    if legacy_error is not None:
        detail["error"] = legacy_error
    return HTTPException(status_code=status_code, detail=detail)
