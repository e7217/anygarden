"""Backward-compatible public REST API error helpers."""

from __future__ import annotations

from typing import Any, Final

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

# Additions are allowed within v1. Renaming or removing one of these values is
# a versioned API change. Keep the registry intentionally limited to the
# machine/task call sites migrated by ANY-3; other HTTPException payloads keep
# their existing endpoint-specific contracts.
PUBLIC_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "MACHINE_ACCESS_DENIED",
        "MACHINE_HAS_ACTIVE_AGENTS",
        "MACHINE_LIST_FORBIDDEN",
        "MACHINE_NOT_FOUND",
        "MACHINE_OFFLINE",
        "MACHINE_REGISTRATION_FORBIDDEN",
        "TASK_ASSIGNEE_NOT_IN_ROOM",
        "TASK_HUMAN_ASSIGNMENT_DISABLED",
        "TASK_INVALID_MUTATION",
        "TASK_NOT_FOUND",
        "TASK_ROOM_NOT_FOUND",
        "TASK_ROOM_PARTICIPANT_REQUIRED",
        "TASK_SOURCE_MESSAGE_NOT_FOUND",
    }
)


class PublicAPIError(HTTPException):
    """HTTP exception whose response adds stable metadata around v1 detail.

    ``detail`` deliberately retains the exact pre-ANY-3 value and type. The
    custom handler adds ``code`` and ``message`` beside it, so consumers that
    read or compare ``body.detail`` continue to work.
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        detail: str | dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.message = message
        self.context = context or {}


def make_api_error(
    code: str,
    message: str,
    status_code: int,
    *,
    legacy_error: str | None = None,
    **extra: Any,
) -> PublicAPIError:
    """Build an additive error response without changing legacy ``detail``.

    String-detail endpoints keep the same string. The machine-delete conflict
    keeps its historical object, including ``error`` and ``agent_count``.
    Machine-readable metadata is added at the response top level by
    :func:`public_api_error_handler`.
    """
    if code not in PUBLIC_ERROR_CODES:
        raise ValueError(f"Unregistered public API error code: {code}")
    reserved = {"code", "detail", "message"}.intersection(extra)
    if reserved:
        raise ValueError(
            "Public API error context cannot replace envelope fields: "
            + ", ".join(sorted(reserved))
        )

    if legacy_error is None:
        detail: str | dict[str, Any] = message
        context = dict(extra)
    else:
        # Preserve the pre-ANY-3 machine-delete detail object byte-for-byte
        # after JSON encoding (apart from insignificant key ordering).
        detail = {
            "error": legacy_error,
            **extra,
            "message": message,
        }
        context = {}

    return PublicAPIError(
        status_code=status_code,
        code=code,
        message=message,
        detail=detail,
        context=context,
    )


async def public_api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render the scoped v1 additive envelope."""
    if not isinstance(exc, PublicAPIError):  # pragma: no cover - registration guard
        raise TypeError("public_api_error_handler requires PublicAPIError")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "message": exc.message,
            **exc.context,
        },
        headers=exc.headers,
    )
