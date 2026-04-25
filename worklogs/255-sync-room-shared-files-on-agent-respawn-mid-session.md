# fix(agent,rooms): sync room shared files on agent respawn & mid-session (#255)

- Commit: `3de57a8` (3de57a812bd5e7fd805b879ab0d3a8c095e6f72c)
- Author: Changyong Um
- Date: 2026-04-25T02:10:27+09:00
- PR: #255

## Situation

`#246`이 도입한 룸 공유 파일(`memory/shared/`)이 실제 사용 시 agent의 system prompt까지 도달하지 못하는 문제가 테스트룸2 실증으로 확인됐다. 업로드 파이프라인(cluster → machine daemon)은 정상 작동해 파일이 `<agent_root>/memory/shared/`에 잘 materialize됐지만, agent subprocess는 "파일이 안 보인다"고 답했다. 조사 결과 세 층의 서로 다른 결함이 같은 증상을 만들고 있었고, 하나만 고쳐서는 실사용에서 여전히 재현됐다.

## Task

- `compose_memory_suffix`의 경로 오계산으로 `<shared-context>` 블록이 아예 생성되지 않던 문제를 고친다.
- agent가 respawn될 때 spawner가 `memory/` 전체를 prune하는데 cluster는 "첫 가입" 시에만 backfill을 쏘므로, respawn 후엔 기존 파일이 영영 복구되지 않는 상황을 해결한다.
- Codex 어댑터가 방별 첫 턴에만 `<shared-context>` 주입을 캐시해서, 업로드/삭제/backfill이 뒤늦게 도착해도 세션 프롬프트에 반영되지 않는 구조적 결함을 바로잡는다.
- 세 수정을 한 PR로 묶되, 서로 직교하게 만들어 회귀 표면이 곱해지지 않도록 한다.

## Action

- `packages/agent/doorae_agent/integrations/base.py:105-121` — `compose_shared_context_block(Path.cwd() / "memory" / "shared")` → `Path.cwd().parent / "memory" / "shared"`. spawner가 cwd를 `<agent_root>/workspace/`로 고정하기 때문에 `memory/shared`는 cwd의 형제 디렉토리. 주석도 교정.
- `packages/agent/doorae_agent/integrations/codex.py:212, 306-332` — `_memory_injected: set[str]` → `dict[str, str]`로 전환. key=room_id, value=마지막 주입 블록의 sha256. 매 턴 `compose_memory_suffix` 재계산 후 sha 비교해 불일치면 `[공유 자료 업데이트]` 라벨과 함께 delta를 turn_content prefix로 1회 주입. 첫 턴은 라벨 없이 원래 형식 유지(#237 호환).
- `packages/cluster/doorae/scheduler/lifecycle.py:11, 34-67, 170-216, 248-302` — `AgentLifecycle.__init__`에 `room_files_dir: Path | None = None` 인자 추가. `handle_report_actual_state`에서 `actual_state != old_state and new_state == "running"` 전환을 감지해 `backfill_targets`에 수집, commit 이후 `_backfill_shared_files_for_agents`로 flush. 각 agent의 모든 room을 조회해 `shared_files_service.backfill_agent`를 호출하며 per-(agent, room) 예외는 로그 후 swallow.
- `packages/cluster/doorae/app.py:337-347` — `AgentLifecycle` 생성 시 `room_files_dir=config.room_files_dir` 전달.
- `packages/agent/tests/test_integrations/test_codex.py:452-580` — `TestCodexSharedContextReinjection` 클래스 신설. 첫 턴 주입 / 동일 블록 스킵 / 변경 블록 delta 주입 / 빈 블록 / 룸별 독립 캐시 5개 케이스.
- `packages/cluster/tests/test_lifecycle.py:688-879` — `TestSharedFilesBackfillOnRunningTransition` 신설. pending→running 전환 시 fan-out 프레임 발행 / running→running 재전송 안 함 / `room_files_dir` 미설정 시 graceful skip 3개 케이스.

## Decisions

`.tmp/plan-255-shared-files-respawn-sync.md`에 기록된 세 결정을 실제 코드에 반영.

- **B 구현 위치**: router의 `ensure_agent_in_room` 확장 vs lifecycle 훅. **lifecycle 훅**을 채택 — respawn/reconnect/placement의 경계를 모두 관찰하는 단일 계층이기 때문. router에서 "새 가입 아니지만 backfill 필요"를 판단하려면 결국 lifecycle 상태를 참조해야 해서 의미가 동일한데 위치만 어색해짐.
- **C 주입 방식**: 매 턴 전량 재주입 vs sha 비교 후 delta만. **sha 비교 + delta prefix** 채택 — Codex thread가 히스토리를 누적하므로 동일 블록을 매 턴 넣으면 토큰 낭비 + 중복 "policy" 텍스트. `#237`의 "no noisy repeat" 원칙은 유지하되, 변경 감지만 추가.
- **범위**: A만 PR로 vs 세 버그 한 PR. **한 PR** 채택 — A만 내보내면 respawn 시 동일 증상이 실사용에서 재현됨을 실측으로 확인. "고쳤다"의 기준은 기능 동작이지 diff 크기가 아님.
- **기각한 대안 (C)**: cluster → agent로 `shared_changed` 전용 WS 프레임 신설. 프로토콜 변경은 machine daemon 롤아웃 순서 이슈가 있고, 같은 효과를 현재 인프라(sha 비교)로 얻을 수 있어 불채택.
- **claude-code는 이번 PR에서 보강 안 함**: SDK가 `resume=session_id`로 기존 세션을 이어받을 때 매 턴 넘기는 `system_prompt` 변경을 실제로 반영하는지는 실측 영역. A 수정만으로 혜택을 받으므로 일단 그 범위까지만 처리하고, 실측 후 미반영이면 후속 PR에서 Codex와 동일한 delta-prefix 패턴을 적용하기로 결정. `(unclear from available context — 수동 실측 필요)`
- **미해결/재검토 트리거**: (1) placement 훅이 spawn 완료보다 빠르면 daemon이 `(AttributeError, KeyError)`로 프레임을 조용히 drop할 수 있음 — 현재 멱등성으로 무해하지만 재발 시 짧은 재시도 또는 daemon pending 버퍼 고려. (2) shared 블록이 바뀔 때마다 system prompt 앞단은 유지되고 뒷단이 바뀌므로 LLM 프롬프트 캐시 키 체계 확인 필요.

## Result

- 새로 추가된 테스트 8개(codex 5 + lifecycle 3) 모두 녹색.
- 회귀 확인: cluster 737 / agent 274 / machine 304 통과. e2e는 doorae_machine 미설치로 기존부터 수집 단계 실패(무관).
- 실사용 기대 시나리오: agent respawn → `handle_report_actual_state(running)` → `_backfill_shared_files_for_agents` → daemon이 `memory/shared/` 재채움 → 다음 턴의 `compose_memory_suffix`가 `<shared-context>` 블록 생성(Layer A) → Codex는 sha 변경 감지해 `[공유 자료 업데이트]` prefix로 재주입(Layer C).
- 후속 작업 pending: (1) 테스트룸2에서 respawn → 질문 흐름 수동 재확인. (2) claude-code SDK `resume`의 system_prompt 반영 실측 후 필요 시 delta-prefix 추가.
