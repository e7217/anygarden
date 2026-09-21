"""룸별 마지막 수신 ``seq``를 respawn 너머로 durable하게 보존.

``ChatClient``는 룸별 마지막 seq를 인메모리로만 들고 있어서, 프로세스가
재시작되면 ``since_seq=0``으로 접속했다. 서버의 replay는 ``since_seq>0``일
때만 동작하므로, 에이전트가 꺼져 있는 동안 도착한 멘션·태스크 배정은
영영 전달되지 않았다(2026-09-19 라이브 확인).

커서를 **에이전트 cwd 아래 파일**에 저장한다. 머신 materializer는 에이전트
루트 바로 아래의 산출물을 prune하지 않으므로 이 파일은 respawn을 넘어
살아남는다 — ``integrations/engine_session_store`` 와 같은 전략이다.

Best-effort: 손상/부재 파일은 빈 매핑으로 degrade하며, 그 경우 동작은
이 변경 이전(모두 0에서 시작)과 같다.
"""

from __future__ import annotations

import json
from pathlib import Path

_STORE_FILENAME = ".anygarden-room-cursors.json"


def _store_path(cwd: Path) -> Path:
    return cwd / _STORE_FILENAME


def load_cursors(cwd: Path) -> dict[str, int]:
    """Return the persisted ``room_id -> last seq`` map (empty on any error)."""
    try:
        raw = _store_path(cwd).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): v
        for k, v in data.items()
        # ``bool`` is an ``int`` subclass — a stray ``true`` must not
        # become a cursor of 1 and silently skip a message.
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0
    }


def save_cursors(cwd: Path, cursors: dict[str, int]) -> None:
    """Atomically persist ``cursors`` to the agent cwd. Best-effort (never raises)."""
    path = _store_path(cwd)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cursors), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Persistence is an optimisation; a failure here must never crash a turn.
        try:
            tmp.unlink()
        except OSError:
            pass
