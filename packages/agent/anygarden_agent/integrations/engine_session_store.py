"""#526 — 룸별 엔진 세션 핸들을 respawn 너머로 durable하게 보존.

엔진 어댑터는 룸별 resume 핸들(codex ``thread_id`` / claude ``session_id``)을
인메모리 dict에 캐시한다. 프로세스 respawn 시 이 매핑이 소실되어, 엔진의
on-disk 세션 스토어가 살아남았더라도 fresh 대화로 시작한다.

이 매핑을 **에이전트 cwd 아래 파일**에 저장한다. 머신 materializer는
"agent-created output directly under the agent root"를 prune하지 않고 보존하므로
(``anygarden_machine.spawner`` 참조), 이 파일은 respawn을 넘어 살아남는다.
respawn된 어댑터는 이를 읽어 핸들을 복원하고 cold 대신 ``resume`` 할 수 있다.

Best-effort: 손상/부재 파일은 빈 매핑으로 degrade하고, 사라진 세션에 대한
resume은 여전히 fresh 턴으로 폴백한다 — 따라서 #526 이전(인메모리 전용)보다
나빠지는 경우는 없다.

한계(part 2, 별도): codex가 ``.codex/*`` 오버레이로 ``CODEX_HOME``을
per-agent ``.codex``로 리다이렉트한 경우, 그 ``.codex``(세션 스토어 포함)는 매
materialize마다 prune된다. 그런 에이전트는 이 매핑을 복원해도 스토어가 없어
resume이 실패(→ fresh 폴백)한다. 세션 스토어 자체의 respawn 보존은 머신 측
변경이며 라이브 검증이 필요해 이 변경 범위에서 제외한다.
"""
from __future__ import annotations

import json
from pathlib import Path

_STORE_FILENAME = ".anygarden-engine-sessions.json"


def _store_path(cwd: Path) -> Path:
    return cwd / _STORE_FILENAME


def load_sessions(cwd: Path) -> dict[str, str]:
    """Return the persisted ``room_id -> session_handle`` map (empty on any error).

    Never raises — a missing, unreadable, or malformed store degrades to an
    empty map so a fresh agent behaves exactly like the pre-#526 in-memory
    default.
    """
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
        if isinstance(k, str) and isinstance(v, str) and v
    }


def save_sessions(cwd: Path, mapping: dict[str, str]) -> None:
    """Atomically persist ``mapping`` to the agent cwd. Best-effort (never raises).

    Written via a temp file + ``replace`` so a crash mid-write can't leave a
    truncated store that would poison the next load.
    """
    path = _store_path(cwd)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(mapping), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        # Persistence is an optimisation; a failure here must never crash a turn.
        try:
            tmp.unlink()
        except OSError:
            pass
