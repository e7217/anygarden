# fix(cluster): bump agent generation on skill attach/detach/delete/update (#119)

- Commit: `6b2b688` (6b2b6886daee936bf5375a4d46f24d7f755e61ea)
- Author: Changyong Um
- Date: 2026-04-19T00:34:04+09:00
- PR: #119 (follow-up to the Phase 1 MVP landed in PR #121)

## Situation

Phase 1 MVP (#121) 를 배포한 뒤 실사용 중 드러난 회귀: admin 이 이미 running
중인 에이전트에 skill 을 attach 해도 머신 디스크에 `skills/<name>/SKILL.md`
파일이 materialize 되지 않았다. 서버 DB / `manifest.json` 은 모두 최신 상태
였지만, 머신 데몬의 `_reconcile_agent` 가 `current_gen >= manifest.generation`
을 no-op 으로 취급하고 materializer 를 다시 돌리지 않아 디스크만 stale 로
남은 상황. 사용자가 `doorae-machine` 을 재기동해서 우회했지만 정상
워크플로가 아니다. 다른 mutation API (agents.py 의 파일 업데이트) 는
`lifecycle.bump_generation` 을 호출해 이 경로를 trigger 하고 있었고,
skill API 에서만 빠져 있었다.

## Task

- attach / detach / delete / register(재등록) 네 경로에서 영향받는 agent
  의 generation 을 올려 머신 데몬이 re-materialize 하게 만든다.
- idempotent no-op (이미 붙어있던 pair 재-attach, 안 붙어있던 pair detach,
  동일 body 재-register) 에서는 bump 하지 않아 불필요한 respawn 을 피한다.
- 새로운 로직이 실수로 역행하지 않도록 TDD 로 네 경로 각각 positive +
  no-op negative 케이스를 테스트로 고정한다.

## Action

- `packages/cluster/doorae/skills_library/service.py`
  - 새 dataclass `RegisterResult(entry, body_changed)` 추가. `register` 가
    이를 반환하도록 시그니처 변경 (기존 `SkillLibraryEntry` 반환에서).
    `body_changed` 는 upsert 시 기존 row 의 `content_hash` 와 비교해 계산.
  - `attach` / `detach` 를 `bool` 반환으로 변경 — 실제로 row 가 insert /
    delete 됐을 때만 True 반환 (idempotent no-op 은 False).
- `packages/cluster/doorae/api/v1/skills.py`
  - 헬퍼 `_lifecycle(request)` 추가 — `app.state.agent_lifecycle` 에서
    `AgentLifecycle` 꺼내 온다.
  - 헬퍼 `_attached_agent_ids(db, skill_id)` 추가 — delete / register 경로
    에서 재사용.
  - `register_skill`: `result.body_changed` 가 True 일 때 attached agents
    전부 bump.
  - `delete_skill`: DB delete **전에** `_attached_agent_ids` snapshot,
    CASCADE 후 snapshot 의 각 agent bump.
  - `attach_skill`: `service.attach` 가 True 반환할 때만 해당 agent bump.
  - `detach_skill`: `service.detach` 가 True 반환할 때만 해당 agent bump.
- 테스트
  - `test_skills_library_service.py` 기존 케이스 4 개를 새 반환 타입에 맞게
    업데이트 (`result.entry.id`, `result.body_changed`). 새 케이스 2 개 추가:
    `test_register_upsert_with_changed_body_reports_changed`,
    `test_detach_noop_returns_false`.
  - `test_skills_library_api.py` fixture 에 `fetcher` 를 `app_state` 로
    expose — body 변경 시뮬레이션용. 새 케이스 6 개 추가:
    attach bump / attach idempotent no-bump / detach bump / detach no-op
    no-bump / delete multi-agent bump / register upsert body-changed bump.

## Decisions

- **`service.attach/detach` 가 bool 을 반환하도록** — 대안은 API 에서
  직접 `AgentSkill` 행 존재 여부를 체크하는 방식. 이건 service 의 idempotent
  계약을 API 에서 재구현하는 셈이라 중복을 낳는다. service 가 "실제로
  바뀌었는가" 를 자기 책임으로 알려주는 쪽이 SRP 에 맞고 bump 호출 전
  guard 가 한 줄로 끝난다.
- **`register` 가 `RegisterResult` 를 반환하도록** — 대안 (a) attached
  agents 를 service.register 내부에서 bump — service 가 lifecycle 을 알게
  돼 순환 의존. (b) API 에서 register 후 hash 비교용으로 다시 DB 조회
  — race + 코드 중복. dataclass 반환은 호출 위치에서 필요한 정보 (entry
  + 변경 플래그) 를 atomic 하게 전달해 가장 단순.
- **delete 경로에서 snapshot → delete → bump 순서** — 역순 (delete →
  snapshot) 은 CASCADE 로 `agent_skills` 가 비워져 snapshot 이 항상
  빈 리스트가 된다. snapshot → delete → bump 순서로 잠시 "bump 는 했으나
  이미 unattached" 인 레이스가 발생할 수 있지만, bump_generation 은 agent
  한 row 만 건드리므로 의미 있는 부작용 없음.
- **register(new row) 경로에서 body_changed=True 반환** — 새 row 는 아직
  attached agent 가 없어 bump 호출 리스트가 비어있지만, 의미상 "bump
  decision 은 호출자 몫" 로 일관 유지. 명시적 이중 if 문을 쓰는 대신
  RegisterResult 의 기본 의미 ("body 가 바뀌었나") 에 충실하게 둔다.

**가정** — (1) `lifecycle.bump_generation` 은 idempotent 하게 여러 번
호출해도 안전하고, generation 증가는 sync frame 전송을 포함한다. (2)
bump 호출은 N 개 agent 에 대해 순차 실행 — 현재 production 규모에서 문제
없음. 수천 agent 에 연결된 스킬을 삭제하면 요청이 길어질 수 있는데 그 땐
`asyncio.gather` 로 바꾸거나 백그라운드 queue 로 이동.

**위반 시 재검토 트리거** — lifecycle.bump_generation 이 side-effect-free
하지 않게 바뀌면 (예: 예외 발생) 여러 agent bump 중 중간 실패 시 일부만
bump 된 상태로 남는다. 그 땐 bump 실패를 로그로 남기고 계속 진행하거나,
트랜잭션 단위로 묶는 후처리가 필요.

## Result

- 443 개 cluster pytest 통과 (신규 bump 테스트 8 개 포함, 기존 14 개 유지).
- `ruff` 변경 파일 전부 clean.
- 시나리오별 동작: attach → bump 1 / re-attach → no bump / detach (linked)
  → bump 1 / detach (not linked) → no bump / delete (N agents) → bump N
  / register new → no-op (no attached) / register same body → no bump /
  register changed body → bump all attached.
- 이제 admin 이 에이전트 running 중에 skill 을 toggle 해도 다음 reconcile
  에서 머신 데몬이 materializer 를 다시 돌려 파일이 생긴다. 머신 재기동
  우회가 더 이상 필요 없다.
- Phase 2 (approve workflow), Phase 3 (전체 디렉토리 passthrough), Phase 5
  (검색 프록시) 는 계속 별도 이슈로 진행 예정.
