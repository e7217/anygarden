# fix(cluster): 서버측 per-request liveness watchdog — orphan 가시화·빠른 감지·재dispatch (#481)

- Commit: `b46b912` (b46b912b6e72956fe546a369ff5837fd17da0a68)
- Author: Changyong Um
- Date: 2026-06-22T23:44:27+09:00
- PR: #481

## Situation

사용자가 에이전트에게 보낸 요청이 `handler_finished` 프레임 없이 죽으면(프로세스 사망/행/WS 끊김/generation 교체), cluster가 이를 마감하는 유일 경로는 `sweep_orphaned_requests`(임계 `ORPHAN_THRESHOLD_SEC_DEFAULT=1200`) 하나뿐이었다. 그 경로는 세 가지 빈틈이 있었다: (a) 룸으로 아무것도 broadcast하지 않아(ActivityLog `handler_orphaned`만 남김) 사용자가 무슨 일이 일어났는지 알 수 없고, (b) `handler_orphaned`는 `_REDISPATCH_OUTCOMES`에 없어 재dispatch 대상이 아니며, (c) 1200s 임계가 느려 사용자 관점에서 최대 ~20분 조용한 무응답이 된다(#470과 동일 클래스).

## Task

새 in-memory 타이머를 만들지 않고(재시작에 취약) 기존 DB 기반 orphan sweeper를 확장해 세 빈틈을 닫는다 — 재시작 생존·멱등·fail-soft·backward-compatible을 유지하면서:

1. orphan 발생 시 룸 시스템 notice를 broadcast해 가시화.
2. orphan된 배정(assignment) Task를 1회 재dispatch해 복구(기존 `_maybe_redispatch_task` 코어 재사용).
3. crash 신호 연동 빠른 감지 — `sweep_stale_agents`가 막 `crashed`로 마킹한 에이전트의 in-flight 요청은 1200s를 기다리지 않고 즉시 orphan(죽은 머신 케이스 ~120s 감지).

범위는 cluster 패키지 3개 파일(`scheduler/lifecycle.py`, `app.py`, `ws/handler.py`)로 한정. 순수 in-memory 서브초 watchdog과 per-request generation 태깅(#470 완전 커버)은 후속으로 제외.

## Action

- **`ws/handler.py` 리팩터(동작 불변)**: `_maybe_redispatch_task`의 코어를 `_redispatch_task_by_request_id(db, *, request_id, reason, manager)`로 추출했다. 프레임 게이팅(`handler_finished` + 터미널-non-ok outcome)은 기존 함수에 남기고, AgentTurnTask 매핑 조회·`_MAX_TASK_REDISPATCH=1` 바운드·미해결 상태·human assignee 제외 같은 복구 로직은 코어로 옮겨 프레임 경로와 orphan 경로가 공용한다. 코어는 caller-supplied 세션에서 `flush`만 하고 `commit`은 caller가 결정하며, 실제 재dispatch가 일어났는지 `bool`로 반환한다. `error` 문자열·metric label·로그는 `frame.outcome` 대신 `reason` 파라미터를 쓴다.
- **`scheduler/lifecycle.py` — 반환 구조 + 빠른 경로**: `sweep_orphaned_requests` 반환을 `list[str]` → `list[OrphanedRequest]`(frozen dataclass: `request_id`/`agent_id`/`room_id`)로 확장. 쿼리에 `Agent` outerjoin을 추가하고 row-level WHERE를 `or_(ActivityLog.timestamp < threshold, Agent.actual_state == "crashed")`로 바꿔, crashed 에이전트의 요청은 임계보다 어려도(recent) orphan-eligible이 되게 했다. `HAVING`의 started>0 AND terminal==0 멱등성은 그대로라 이미 종료된 요청은 재orphan되지 않는다.
- **`scheduler/lifecycle.py` — `notify_and_redispatch_orphans(session_factory, manager, rows)` 신규**: orphan row마다 (1) 룸 시스템 notice(`append_message`로 `participant_id=None`, content는 한국어 1줄 `_ORPHAN_NOTICE_TEXT`, metadata `system_origin="liveness_orphan"` + `request_id`)를 append+commit한 뒤 `MessageOut`으로 broadcast하고, (2) `_redispatch_task_by_request_id(reason="liveness_orphan")`로 배정 Task를 복구한다. 라이브 턴(매핑 없음)은 notice만. 각 row를 독립 `try`로 감싸고 notice/redispatch를 별 세션으로 처리해, 한 row 실패가 나머지 row나 sweep의 orphan-마킹 커밋을 절대 막지 않는다(fail-soft). ws↔scheduler / messages↔scheduler 사이클을 피하려 import는 함수 내부 lazy.
- **`app.py` — sweeper 와이어링**: `_run_orphan_sweeper`의 sweep 순서를 `sweep_stale_agents` → `sweep_orphaned_requests`로 바꿔, 같은 사이클에서 막 `crashed`된 에이전트의 요청까지 즉시 orphan되게 했다. `ANYGARDEN_REQUEST_LIVENESS_SEC` env로 느린-경로 임계를 오버라이드(파싱 실패 시 기본 1200). orphaned 결과의 span-reaper 브리지를 `OrphanedRequest.request_id`로 갱신하고, `app.state.connection_manager`(없으면 `None`)를 `notify_and_redispatch_orphans`로 전달 — manager 미주입 시 notice는 스킵하고 redispatch만 수행(graceful degrade).
- **테스트**: `test_scheduler_sweeper.py`에 OrphanedRequest 필드 검증과 빠른 경로 3종(crashed 에이전트의 below-threshold 요청 즉시 orphan / running 에이전트의 recent 요청은 미orphan / crashed라도 터미널 있으면 미orphan)을 추가하고, 기존 반환-타입 assertion을 갱신했다. 신규 `test_liveness_orphan_notify.py`는 배정 orphan→notice+redispatch / 라이브 orphan→notice만 / 한 bad row가 나머지 비차단 / manager 없으면 notice 스킵·redispatch 유지를 커버한다.

## Result

- orphan 처리가 "감지 → 가시화 → 복구" 3단으로 통합됐다. 죽은 머신/프로세스의 in-flight 요청은 이제 ~20분이 아니라 stale-heartbeat reaper 사이클(~120s) 안에 orphan되고, 룸에 한국어 system notice가 뜨며, 배정 유래 턴이면 1회 자동 재dispatch된다.
- 오발화 방지: 빠른 경로는 `sweep_stale_agents`의 dual-gate(heartbeat stale + machine !online)로 마킹된 `crashed`에만 반응하므로 살아있는 느린 머신을 잡지 않고, 느린-경로 임계는 1200s 유지로 정상 장기 턴을 보호한다. notice는 orphan 멱등성 덕에 request_id당 1회.
- 검증: `uv run pytest packages/cluster -q` → **1207 passed, 1 deselected** (신규 7 테스트 포함). `uv run ruff check` 변경 파일 5개 모두 **All checks passed**(패키지 전체 잔여 95건은 base 브랜치와 동일한 기존 lint, 본 PR 범위 밖). 기존 redispatch 10 테스트 green 유지(리팩터 동작 불변 확인).
- 계획 대비 벗어남 없음. 계획 §2가 시사한 `lifecycle.py`의 `import os`는 env를 call-site(app.py, 이미 os 보유)에서 읽는 §4 지침과 충돌해 미사용이 되므로 제거했다.
