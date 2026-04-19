# fix(machine): serialize per-agent reconcile with lock + pre-reservation (#183)

- Commit: `4115c24`
- Author: Changyong Um
- Date: 2026-04-20
- PR: #183

## Situation

`MachineDaemon._reconcile_agent`가 generation 체크를 통과하면 `asyncio.create_task(self._request_token_and_spawn(...))`로 spawn을 백그라운드 dispatch했다. 그러나 `_running_generations[agent_id]`는 spawn **완료** 시점에야 업데이트(daemon.py:344)되므로, 첫 번째 spawn이 token_grant를 기다리는 동안 동일 agent에 대한 두 번째 `sync_desired_state` frame이 도착하면 generation 체크를 또 통과해 **중복 spawn task**가 dispatch됐다. 결과적으로 같은 agent_id로 두 subprocess가 경쟁하고, `_token_futures[agent_id]` 덮어쓰기 race로 한쪽 future는 30초 timeout까지 영원히 resolve되지 않았다.

K8s controller의 per-key work queue 같은 직렬화 보증이 없는 상태 — 2026-04-19 설계 리뷰에서 kubernetes-architect가 P1 항목으로 지적.

## Task

- 동일 agent_id에 대한 reconcile 결정을 직렬화하되 서로 다른 agent는 병렬 처리 유지
- spawn token wait 동안 WS 메시지 루프는 블록되지 않아야 함 (기존 `create_task` deadlock 방지 제약 유지)
- spawn 실패 / token timeout 시 pre-reservation 롤백해 재시도 가능
- `_handle_sync_batch` 고아 kill, `_on_agent_stopped`, `_on_agent_crashed` 경로가 같은 lock을 공유하도록 일관성 확보
- crash restart 경로에서 서버 reconcile과의 이중 spawn 방지

## Action

- `packages/machine/doorae_machine/daemon.py:__init__` — `_agent_locks: dict[str, asyncio.Lock]` 추가. 주석으로 per-agent 격리 이유 명시
- `packages/machine/doorae_machine/daemon.py:_lock_for` (신규 private helper) — `setdefault` 패턴으로 lock 지연 생성
- `packages/machine/doorae_machine/daemon.py:_reconcile_agent` — body 전체를 `async with self._lock_for(agent_id):` 로 감싸고, generation 체크 통과 지점에서 `_running_generations[agent_id] = manifest.generation` 사전 기록. 이후 lock 해제하고 `create_task`로 spawn dispatch — WS 루프는 자유, concurrent reconcile은 reservation을 보고 short-circuit
- `packages/machine/doorae_machine/daemon.py:_request_token_and_spawn` — success 경로의 `_running_generations` 대입 제거(pre-reservation이 이미 했음), token timeout과 spawn failure 양쪽에서 `_rollback_reservation(agent_id, generation)` 호출
- `packages/machine/doorae_machine/daemon.py:_rollback_reservation` (신규) — 해당 lock을 쥐고 `_running_generations[agent_id] == generation`일 때만 pop. 더 높은 gen이 이미 reservation됐으면 no-op (newer reconcile이 이김)
- `packages/machine/doorae_machine/daemon.py:_handle_sync_batch` — 고아 kill 각각을 per-agent lock으로 감싸 pop이 concurrent reservation을 건드리지 않도록
- `packages/machine/doorae_machine/daemon.py:_on_agent_stopped` / `_on_agent_crashed` — `_running_generations.pop`을 lock 하에 수행. crash restart는 lock 하에 `current < manifest.generation` 체크 후 reservation, 조건 불충족 시 restart 건너뛰고 `dispatched=False`로 로그
- `packages/machine/tests/test_daemon.py:TestReconcileSerialization` (신규 클래스, 4 케이스):
  - `test_duplicate_same_generation_spawns_once` — 동일 gen 두 번 입력, spawn 1회만 호출, token_request도 1회
  - `test_stale_generation_ignored_when_reservation_higher` — gen 5 예약 상태에서 gen 3 요청, spawn/kill 미호출, reservation 보존
  - `test_spawn_failure_rolls_back_reservation` — spawn 실패 시 `_running_generations`에서 해당 agent 제거
  - `test_parallel_reconcile_different_agents` — 서로 다른 agent는 병렬 처리, 둘 다 spawn 성공 + reservation 기록

## Decisions

`.tmp/plan-183-per-agent-reconcile-lock.md`의 대안 비교:

- **A — per-agent Lock + pre-reservation + `create_task` 유지** ← 선택
- **B — 단일 전역 Lock**: 모든 agent reconcile이 직렬화 → 대규모에서 latency 급증
- **C — asyncio.Queue per-agent**: Lock으로 충분한 상황에 인프라 과잉
- **D — reservation만 앞당기고 Lock 없음**: `await` 경계에서 dict 경합 가능

결정적 근거: 기존 `create_task` 사용 이유가 "WS 메시지 loop이 spawn 완료까지 블록 방지"(daemon.py:281-285 주석)였는데, lock을 **per-agent**로 쪼개면 같은 agent의 연속 reconcile만 큐잉되고 다른 agent의 handler는 방해받지 않는다 → blocking 우려 해소하면서 race 닫힘.

가정 / 미해결:
- spawn이 token_grant를 기다리는 동안(최대 30초) 같은 agent로 더 높은 gen이 도착하면, 현재 구현은 **기존 in-flight task를 취소하지 않고** reservation만 덮어쓰고 두 번째 dispatch를 허용한다. 결과적으로 첫 task의 spawn 결과는 성공해도 `_rollback_reservation`이 gen 비교에서 no-op이 되므로 상태는 일관적이지만, 두 subprocess가 순간적으로 공존하는 창이 남는다. 근본 해결은 in-flight task 추적 + 취소 로직이 필요하지만 범위 확대 — 본 PR은 "중복 dispatch" 방지에 집중
- `_agent_locks` dict가 agent 종료 시 cleanup되지 않음 (Lock 객체는 작아 누수 영향 미미). 필요 시 후속

## Result

- `uv run pytest` (packages/machine) 250개 통과 (기존 246 + 신규 4)
- 리뷰 P1-3 항목 해결. generation race로 인한 중복 spawn / 고아 token_future / `unexpected_token_grant` 경고 경로 차단
- `_on_agent_crashed` + server reconcile 이중 spawn 경로도 함께 차단 (crash restart의 pre-reservation 체크가 newer server reservation을 존중)
- 관련 이슈 #182(manifest stopped 후 request_replacement)는 같은 manifest_store를 건드리지만 lock 스코프는 generation dict만 → 기능 충돌 없음
