# ANY-4 런타임 성능/회복성 베이스라인 노트

## 범위
- 이벤트/폴링 경로 병목 완화
- DB 쿼리 N+1 제거 및 인덱스 최적화
- 배치 정리/재시작 경로의 병목 안정성 개선

## 베이스라인(사전 기준)
- **측정 상태**: 현재 변경 이전 런타임 쿼리/보고 경로는 다수의 반복 `SELECT`를 발생시켜 동일 프레임 처리량이 에이전트 수에 선형으로 증가.
- **핵심 핫스팟(식별):**
  1. `handle_report_actual_state`: agent_id별 `Agent` 단건 조회 + placed query에서 별도 Python 필터
  2. `send_sync_batch`/`_backfill_shared_files_for_agents`: agent 당 room 조회 N+1
  3. `MachineDaemon._report_actual_state`: `_spawner.list_running()` 호출을 `_flush_memory_updates`, `_flush_outbox_artifacts`, `_prune_stale_transitional`에서 각각 재호출
- **개선 목표(추정):** 동일 프레임 처리에서 DB roundtrip/daemon 이벤트 스캔 횟수를 대폭 감축해 동시 에이전트 수 증가 시 지연 증가 기울기 하향.

## 이번 변경 반영 사항
1. `handle_report_actual_state`에서 보고된 agent_id 전량을 한 번에 배치 조회(
   `SELECT ... WHERE Agent.id IN (...)`) 후 map으로 매핑.
2. 배치 없음 확인 경로를 쿼리에서 `desired_state='stopped'`, `actual_state!='stopped'`, 필요 시 `id NOT IN (...)` 조건으로 축소.
3. `_backfill_shared_files_for_agents`, `send_sync_batch`에서 `Participant.agent_id -> room_id` 조회를 한 번의 집계 쿼리로 전환.
4. 데몬 `report_actual_state` 경로에서 `list_running()` 결과를 재사용하여 메모리 동기/아웃박스/전이 prune가 동일 스냅샷 기반으로 동작.
5. Participant 인덱스 보강 (`ix_participants_agent_room`) + Alembic 마이그레이션 `064_participant_agent_room_index.py` 추가.

## 검증 및 보완
- 빈 `running_agents=[]` 스냅샷을 명시적으로 전달해도 헬퍼가
  `list_running()`을 재호출하지 않는 회귀 테스트를 추가했다.
- Alembic `064`의 `ix_participants_agent_room(agent_id, room_id)` 생성과
  `063` downgrade 시 제거를 검증하는 마이그레이션 테스트를 추가했다.
- 최신 `main@66235f3` 재적용 검증:
  - machine daemon: `68 passed`
  - cluster lifecycle + migrations: `58 passed`
  - 치명 Ruff 규칙(`E9,F63,F7,F82`), `git diff --check`: 통과
  - Alembic head: 단일 `064` (down_revision `063`)

## 남은 측정 갭
1. 변경 전/후 쿼리 횟수 비교 벤치마크(대량 에이전트 시뮬레이션)
2. 보고 처리 중 프로세스 집합이 변하는 동시성 시나리오 테스트
3. 새 인덱스 적용 전후 `EXPLAIN ANALYZE` 기반 쿼리 플랜 비교

## 다음 액션
- 다음 이슈에서 성능 측정 스크립트(동시 에이전트 시나리오 + 쿼리 카운트 로그) 추가 및 리포트 반영.
