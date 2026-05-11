# fix(openhands): auto-reset openhands agents on cluster startup (#379)

- Commit: `13782ce` (13782ce44693a963bcde6aee8ee12e61ee710d8b)
- Author: Changyong Um
- Date: 2026-05-12T00:49:22+09:00
- PR: #379

## Situation

`openhands`는 doorae의 어댑터 중 유일하게 OpenHands SDK `Conversation` 객체를 **에이전트 프로세스 메모리**에 보관하는 in-process 어댑터다 (`packages/agent/doorae_agent/integrations/openhands_engine.py:159` — `self._conversations: dict[str, Any]`). 다른 엔진(`claude-code`, `codex`, `gemini-cli`)은 매 세션마다 CLI 서브프로세스를 spawn하는 구조라 에이전트 프로세스가 재기동되어도 fresh 부팅이 자연스럽다. 그러나 openhands는 프로세스 메모리가 날아가면 `_conversations`가 빈 상태가 되는데 DB의 `actual_state`는 여전히 `running`으로 남아 mismatch가 발생, 사용자가 첫 메시지를 보내도 conversation lookup이 실패한다. 사용자는 매번 에이전트 카드의 stop → start 버튼을 직접 눌러 우회해 왔다.

## Task

- 클러스터 startup 시점에 `engine='openhands'` AND `actual_state ∈ ('running','starting','stopping')`인 에이전트를 자동 식별
- 사용자가 수동으로 하던 stop/start 사이클과 의미적으로 동등한 reset 동작을 수행 — `actual_state='pending'`, `desired_state='running'`, `pid=None`, `placed_on_machine_id=None`
- 다른 엔진 에이전트에는 영향 없도록 격리
- 새 코드 경로/추상화 도입 없이 기존 머신 reconnect → orphan placement 메커니즘 재활용

## Action

- `packages/cluster/doorae/app.py:211-242` — 모듈 레벨에 `_reset_openhands_agents_for_restart(db) -> list[str]` 헬퍼 추가. 조건에 매칭되는 에이전트들의 상태를 orphan으로 전환하고 reset된 ID 리스트를 반환한다 (commit하지 않음 — 호출자가 트랜잭션 책임).
- `packages/cluster/doorae/app.py:430-456` — 기존 startup의 머신 offline reset 블록(`if not engine_provided`) 안에서 새 헬퍼를 호출하고, 같은 commit으로 두 변경을 함께 묶음. reset된 에이전트가 있을 때만 `startup.openhands_agents_reset` 이벤트를 구조화 로그로 남김.
- `packages/cluster/tests/test_startup_openhands_reset.py` (신규, 176줄) — 11개 케이스 단위 테스트:
  - 활성 상태 3종(`running`/`starting`/`stopping`) → orphan으로 전환 (parametrized)
  - 비활성 상태 3종(`stopped`/`pending`/`idle`) → 변경 없음
  - 다른 엔진 3종(`claude-code`/`codex`/`gemini-cli`) + `running` → 변경 없음 (격리 보장)
  - 대상 0개 → 빈 리스트 반환
  - 혼재 시나리오 → openhands+running만 reset, 나머지 모두 unchanged
- 기존 970개 테스트와 ruff 모두 통과.

## Decisions

`.tmp/plan-379-openhands-restart-recovery.md`에서 4개 대안을 비교했다:

- **A. Startup 일괄 orphan reset** (선택) — `app.py` startup에서 openhands 에이전트만 골라 DB 상태를 orphan으로 전환. 머신 reconnect 시 기존 `_place_orphaned_agents` → `request_start` 경로가 자동으로 새 spawn을 트리거.
- **B. 머신 register 시점 bounce** — `_handle_register`에 끼우는 방식. 클러스터만 단독 재시작되어 머신 reconnect가 발생하지 않는 경우 트리거되지 않고, 같은 머신에 여러 엔진이 섞여 있을 때 책임 배치가 잘못된다고 판단해 기각.
- **C. 어댑터 lazy 복원** — 첫 메시지에서 `_conversations`에 없으면 새 Conversation 생성. SDK가 디스크에서 conversation 히스토리를 복원하는 정식 API가 있는지 검증이 필요하고, 없다면 stop/start와 동일한 효과라 1차 패치 범위 밖으로 분리.
- **D. 어댑터별 health-check 프로토콜** — 머신↔클러스터 프레임 추가가 필요해 과대 설계로 판단.

**결정적 근거**: 기존 orphan placement 경로(`ws/machine_handler.py:244-267`)가 이미 검증돼 있어 DB 상태만 살짝 비틀어 두면 새 코드 경로 없이 동일 메커니즘에 편승할 수 있다. 또한 `start_agent` API 핸들러(`api/v1/agents.py:814-821`)가 동일한 reset 패턴을 이미 사용하므로 사용자가 수동 stop/start를 누르는 것과 의미적으로 정확히 동등하다.

**가정 (재검토 트리거)**:
- 서버 재시작의 실제 의미는 클러스터+머신 동시 재기동이 대부분이라는 전제. 클러스터만 단독 재기동되고 openhands 프로세스가 살아있는 경우 불필요한 bounce가 발생하나 사용자 체감은 수동 stop/start와 동일.
- `stopping` 상태도 reset 대상에 포함했다 — startup 시점에 잔존하는 stopping은 정상 종료 흐름이 끊긴 좀비라 다시 살리는 게 안전하다고 판단.
- OpenHands SDK가 conversation 히스토리를 영속화하는지는 미검증 (옵션 C 후속 이슈).

## Result

- openhands 에이전트가 클러스터 재시작 후 사용자 수동 조작 없이 자동으로 fresh 상태로 respawn된다.
- 다른 엔진(`claude-code`, `codex`, `gemini-cli`)에 대한 회귀 없음 — 단위 테스트로 격리 보장.
- `cluster` 전체 회귀 테스트 970 passed, ruff clean.
- 후속 이슈 후보: Option B(머신 register 정밀 bounce), Option C(어댑터 lazy 복원), Option D(health-check 프로토콜).
