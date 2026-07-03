"""Agent unavailability reasons — the single source of truth (#516).

An agent can be *desired* ``running`` yet be unable to respond: no machine
supports its engine, the spawn failed, it crashed without recovery, its DB
engine drifted from the running process, or it belongs to no room. Today none
of that surfaces — the user sees silence and the admin sees a reasonless
``pending`` badge.

This module defines the machine-readable ``unavailable_code`` vocabulary and
derives the human-facing message from ``(code, detail, audience)``. The message
is deliberately NOT stored on the row so it can be translated later and gated
by audience: ``"admin"`` sees the raw failure (stderr, exit code); ``"user"``
(a room participant) only gets a short, non-technical nudge.

Consumed by:
- ``scheduler/lifecycle.py`` — writes ``Agent.unavailable_*`` at each
  not-running transition.
- ``api/v1/agents.py`` — renders the admin-facing ``AgentOut.unavailable_reason``.
- ``ws/handler.py`` — the reactive room notice + coarse presence label.
"""

from __future__ import annotations

from typing import Mapping, Optional

# ── Reason codes ────────────────────────────────────────────────────────
# NULL ``unavailable_code`` means the agent is fine. A non-NULL code means
# "desired running but cannot respond, for this reason".
NO_MACHINE_FOR_ENGINE = "no_machine_for_engine"
SPAWN_FAILED = "spawn_failed"
ENGINE_MISMATCH = "engine_mismatch"
CRASHED = "crashed"
NO_ROOM = "no_room"

UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {
        NO_MACHINE_FOR_ENGINE,
        SPAWN_FAILED,
        ENGINE_MISMATCH,
        CRASHED,
        NO_ROOM,
    }
)

_AUDIENCES = frozenset({"user", "admin"})


def render_unavailable_message(
    code: Optional[str],
    detail: Optional[Mapping[str, object]],
    *,
    audience: str = "user",
) -> str:
    """Render a human-facing message for an unavailability ``code``.

    ``audience="user"`` (default, the safe choice) returns a short, non-technical
    line safe to show a room participant — never stderr or exit codes.
    ``audience="admin"`` appends the concrete failure so an operator can act.

    Tolerates ``detail=None`` (NULL column) and unknown codes (returns a generic
    fallback rather than raising) so a future code never breaks a live render.
    """
    if audience not in _AUDIENCES:
        raise ValueError(f"unknown audience: {audience!r}")
    d: Mapping[str, object] = detail or {}

    if code == NO_MACHINE_FOR_ENGINE:
        engine = d.get("engine")
        who = f"'{engine}' 엔진" if engine else "이 엔진"
        return f"{who}을 지원하는 실행 환경이 없어 대기 중입니다."

    if code == SPAWN_FAILED:
        base = "실행 환경 시작에 실패해 대기 중입니다."
        if audience == "admin":
            return _with_admin_trace(base, d)
        return base

    if code == ENGINE_MISMATCH:
        if audience == "admin":
            db_engine = d.get("db_engine")
            running = d.get("running_engine")
            return (
                f"실행 중 엔진('{running}')이 설정('{db_engine}')과 달라 "
                "재시작이 필요합니다."
            )
        return "설정 변경 반영을 위해 재시작이 필요합니다."

    if code == CRASHED:
        base = "오류로 중단되었습니다."
        if audience == "admin":
            exit_code = d.get("exit_code")
            suffix = f" (exit={exit_code})" if exit_code is not None else ""
            return _with_admin_trace(base + suffix, d)
        return base

    if code == NO_ROOM:
        return "배정된 방이 없습니다."

    # Unknown / future code — never raise on a live render path.
    return "지금 응답할 수 없는 상태입니다."


def _with_admin_trace(base: str, detail: Mapping[str, object]) -> str:
    """Append the raw ``stderr_tail`` for admin audiences when present."""
    stderr = detail.get("stderr_tail")
    if stderr:
        return f"{base}\nstderr: {stderr}"
    return base


def room_notice_for_unavailable(
    agent_name: str,
    code: Optional[str],
    detail: Optional[Mapping[str, object]],
) -> str:
    """One-line room system notice shown to a user who messaged an agent
    that can't respond (#516).

    Always the ``user`` audience — never leaks stderr into the room. Names the
    agent so a multi-agent room's notice is unambiguous.
    """
    label = render_unavailable_message(code, detail, audience="user")
    return f"⚠️ {agent_name}: {label} (관리자에게 문의하세요.)"
