"""Tests for the agent unavailability reason vocabulary (#516).

``agent_availability`` is the single source of truth for the machine-readable
reason codes and the human-facing message derived from them. The message is
NOT stored — it is rendered from ``(code, detail, audience)`` so translation /
audience gating stays flexible.
"""

from __future__ import annotations

import pytest

from anygarden.agent_availability import (
    CRASHED,
    ENGINE_MISMATCH,
    NO_MACHINE_FOR_ENGINE,
    NO_ROOM,
    SPAWN_FAILED,
    UNAVAILABLE_CODES,
    render_unavailable_message,
    room_notice_for_unavailable,
)


def test_all_codes_are_registered() -> None:
    assert UNAVAILABLE_CODES == {
        NO_MACHINE_FOR_ENGINE,
        SPAWN_FAILED,
        ENGINE_MISMATCH,
        CRASHED,
        NO_ROOM,
    }


def test_no_machine_message_names_the_engine() -> None:
    detail = {"engine": "codex-cli"}
    user_msg = render_unavailable_message(NO_MACHINE_FOR_ENGINE, detail, audience="user")
    admin_msg = render_unavailable_message(NO_MACHINE_FOR_ENGINE, detail, audience="admin")
    assert "codex-cli" in user_msg
    assert "codex-cli" in admin_msg


def test_engine_mismatch_message_mentions_restart() -> None:
    detail = {"db_engine": "codex-cli", "running_engine": "codex"}
    user_msg = render_unavailable_message(ENGINE_MISMATCH, detail, audience="user")
    admin_msg = render_unavailable_message(ENGINE_MISMATCH, detail, audience="admin")
    # user gets a short, non-technical nudge
    assert "재시작" in user_msg
    # admin sees the concrete divergence
    assert "codex" in admin_msg and "codex-cli" in admin_msg


def test_spawn_failed_hides_stderr_from_user_but_shows_admin() -> None:
    stderr = "Traceback: Unknown engine 'codex-cli'"
    detail = {"engine": "codex-cli", "stderr_tail": stderr}
    user_msg = render_unavailable_message(SPAWN_FAILED, detail, audience="user")
    admin_msg = render_unavailable_message(SPAWN_FAILED, detail, audience="admin")
    # stderr is sensitive/technical: never leaks to end users
    assert stderr not in user_msg
    # admin needs the raw failure to act
    assert stderr in admin_msg


def test_crashed_shows_exit_code_to_admin_only() -> None:
    detail = {"exit_code": 137, "stderr_tail": "killed"}
    user_msg = render_unavailable_message(CRASHED, detail, audience="user")
    admin_msg = render_unavailable_message(CRASHED, detail, audience="admin")
    assert "137" not in user_msg
    assert "137" in admin_msg


def test_no_room_message_is_stable_without_detail() -> None:
    msg = render_unavailable_message(NO_ROOM, {}, audience="user")
    assert msg  # non-empty, no KeyError on empty detail


def test_none_detail_is_tolerated() -> None:
    # detail may be NULL in the DB
    msg = render_unavailable_message(NO_MACHINE_FOR_ENGINE, None, audience="user")
    assert msg


def test_unknown_code_falls_back_gracefully() -> None:
    msg = render_unavailable_message("some_future_code", {}, audience="user")
    assert msg  # generic fallback, never raises


def test_audience_defaults_to_user() -> None:
    stderr = "secret trace"
    detail = {"engine": "x", "stderr_tail": stderr}
    # default audience must be the safe (non-leaking) one
    assert stderr not in render_unavailable_message(SPAWN_FAILED, detail)


def test_invalid_audience_rejected() -> None:
    with pytest.raises(ValueError):
        render_unavailable_message(NO_ROOM, {}, audience="root")


def test_room_notice_names_agent_and_hides_stderr() -> None:
    stderr = "secret trace"
    notice = room_notice_for_unavailable(
        "Nova", SPAWN_FAILED, {"engine": "codex-cli", "stderr_tail": stderr}
    )
    assert "Nova" in notice
    assert stderr not in notice
    assert "관리자" in notice

