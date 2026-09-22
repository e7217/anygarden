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

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

# ── Reason codes ────────────────────────────────────────────────────────
# NULL ``unavailable_code`` means the agent is fine. A non-NULL code means
# "desired running but cannot respond, for this reason".
NO_MACHINE_FOR_ENGINE = "no_machine_for_engine"
SPAWN_FAILED = "spawn_failed"
ENGINE_MISMATCH = "engine_mismatch"
CRASHED = "crashed"
NO_ROOM = "no_room"
INVALID_PROVIDER = "invalid_provider"
INVALID_ENDPOINT = "invalid_endpoint"

UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {
        NO_MACHINE_FOR_ENGINE,
        SPAWN_FAILED,
        ENGINE_MISMATCH,
        CRASHED,
        NO_ROOM,
        INVALID_PROVIDER,
        INVALID_ENDPOINT,
    }
)

_AUDIENCES = frozenset({"user", "admin"})


def render_unavailable_message(
    code: str | None,
    detail: Mapping[str, object] | None,
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

    if code == INVALID_ENDPOINT:
        return "직접 모델 연결 설정이나 실행 환경을 확인해야 합니다. 관리자에게 설정 확인을 요청하세요."

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

    if code == INVALID_PROVIDER:
        return "Pi 에이전트의 Provider 설정을 보완한 뒤 시작해 주세요."

    if code == NO_ROOM:
        return "배정된 방이 없습니다."

    if code == QUOTA_EXHAUSTED:
        until = d.get("until")
        base = "사용량 한도로 잠시 응답할 수 없습니다."
        if until:
            base = f"사용량 한도로 잠시 응답할 수 없습니다({until} 이후 복구 예정)."
        elif d.get("reset_known"):
            base = "사용량 한도로 잠시 응답할 수 없습니다(복구 시각 추정 중)."
        if audience == "admin":
            return _with_admin_trace(base, d)
        return base

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
    code: str | None,
    detail: Mapping[str, object] | None,
) -> str:
    """One-line room system notice shown to a user who messaged an agent
    that can't respond (#516).

    Always the ``user`` audience — never leaks stderr into the room. Names the
    agent so a multi-agent room's notice is unambiguous.
    """
    label = render_unavailable_message(code, detail, audience="user")
    return f"⚠️ {agent_name}: {label} (관리자에게 문의하세요.)"


# ── Quota availability (D-2, #625) ─────────────────────────────────────
# Raft-pattern: a quota-blocked agent is a *first-class availability fact*
# ("blocked + reset time"), so orchestrators can pre-emptively route around
# it instead of discovering the failure per-turn.
QUOTA_EXHAUSTED = "quota_exhausted"

UNAVAILABLE_CODES = frozenset({*UNAVAILABLE_CODES, QUOTA_EXHAUSTED})

# Closed, provider-agnostic signature list. The classifier is deliberately
# conservative: unknown errors never block routing (fail open on the
# availability side — the engine call itself still fails closed per-turn).
_QUOTA_SIGNATURES: tuple[str, ...] = (
    "usage limit",
    "usage_limit",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "429",
    "too many requests",
    "resource_exhausted",
    "insufficient",
    "billing",
    "monthly cap",
    "spending limit",
    "exceeded your current quota",
)
_RESET_PATTERNS: tuple[tuple[str, str], ...] = (
    # "resets at 2026-09-18T05:00:00Z" / "reset at 05:00 UTC"
    (r"resets? at\s+(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)", "iso"),
    # "in 12 minutes" / "in 2 hours" / "in 90 seconds"
    (r"in\s+(\d+)\s*(seconds?|minutes?|hours?)", "delta"),
)


def classify_quota_error(error_text: str | None) -> bool:
    """True when ``error_text`` looks like a quota/usage-limit failure."""
    if not error_text:
        return False
    lowered = error_text.lower()
    return any(signature in lowered for signature in _QUOTA_SIGNATURES)


def quota_reset_from_error(
    error_text: str | None, *, now: datetime
) -> datetime | None:
    """Best-effort reset-time extraction from an error string.

    Returns ``None`` when the provider gave no bound — the agent stays
    blocked without a promised instant and only a successful call clears it.
    """
    if not error_text:
        return None
    for pattern, kind in _RESET_PATTERNS:
        match = re.search(pattern, error_text, flags=re.IGNORECASE)
        if not match:
            continue
        if kind == "iso":
            raw = match.group(1).replace(" ", "T")
            if not re.search(r"(Z|[+-]\d{2}:?\d{2})$", raw):
                raw += "Z"
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        value = int(match.group(1))
        unit = match.group(2).lower()
        seconds = value * (3600 if unit.startswith("hour") else 60 if unit.startswith("min") else 1)
        return now.astimezone(UTC) + timedelta(seconds=seconds)
    return None


def quota_window_active(
    code: str | None, until: datetime | None, *, now: datetime
) -> bool:
    """Whether a quota block still binds at ``now``.

    False once the promised instant passed — callers holding a session
    should also clear the columns then (``clear_quota_if_lapsed``).
    """
    if code != QUOTA_EXHAUSTED:
        return False
    if until is None:
        return True
    now_utc = now if now.tzinfo else now.replace(tzinfo=UTC)
    until_utc = until if until.tzinfo else until.replace(tzinfo=UTC)
    return now_utc < until_utc


def mark_quota_exhausted(agent: Any, error_text: str | None, *, now: datetime) -> None:
    """Stamp the quota block on an Agent row (caller commits).

    ``unavailable_since`` keeps its value while the code is unchanged, so it
    reflects when *this* block began. A sharper reset instant always wins.
    """
    reset = quota_reset_from_error(error_text, now=now)
    if agent.unavailable_code != QUOTA_EXHAUSTED:
        agent.unavailable_since = now
    elif (
        reset is not None
        and agent.unavailable_until is not None
        and reset < agent.unavailable_until
    ):
        reset = agent.unavailable_until  # never shorten a live window
    agent.unavailable_code = QUOTA_EXHAUSTED
    agent.unavailable_detail = {
        # Key matches the existing admin-trace convention so
        # ``_with_admin_trace`` appends it for operators.
        "stderr_tail": (error_text or "")[-512:],
        "reset_known": reset is not None,
    }
    agent.unavailable_until = reset


def clear_quota_block(agent: Any) -> bool:
    """Clear a quota block (successful call or lapse). True when changed."""
    if agent.unavailable_code != QUOTA_EXHAUSTED:
        return False
    agent.unavailable_code = None
    agent.unavailable_detail = None
    agent.unavailable_until = None
    agent.unavailable_since = None
    return True


def clear_quota_if_lapsed(agent: Any, *, now: datetime) -> bool:
    """Lazily clear a quota block whose promised instant has passed."""
    if not quota_window_active(agent.unavailable_code, agent.unavailable_until, now=now):
        return clear_quota_block(agent)
    return False


def routing_blocked(agent: Any, *, now: datetime) -> bool:
    """Pre-avoidance predicate for orchestrators (D-3 consumes this).

    Any unavailability code blocks routing *except* a quota window whose
    promised instant already passed — that one reads as available (and the
    caller should persist the lazy clear).
    """
    if agent.unavailable_code is None:
        return False
    if agent.unavailable_code == QUOTA_EXHAUSTED:
        return quota_window_active(agent.unavailable_code, agent.unavailable_until, now=now)
    return True
