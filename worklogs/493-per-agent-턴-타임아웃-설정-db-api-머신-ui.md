# feat(agent-settings): per-agent 턴 타임아웃 설정 (DB·API·머신·UI) (#493)

- Commit: `e0bd0a8` (e0bd0a8badd8d772643a57ff8375bd640c856298)
- Author: Changyong Um
- Date: 2026-06-23T20:51:59+09:00
- PR: #493

## Situation

#492가 엔진 어댑터 턴 타임아웃을 글로벌 env로 조정 가능하게 만들었지만, 운영자가 에이전트별로 다르게 주거나 web UI에서 조정할 방법은 없었다. 긴 응답이 필요한 특정 에이전트만 타임아웃을 늘리고 싶어도 배포 전역 env밖에 없었다. `Agent` 모델에는 타임아웃 컬럼이 전무했고, 모든 타임아웃이 전역이었다.

## Task

- `Agent`에 per-agent 턴 타임아웃 컬럼을 추가하고 web UI에서 편집 가능하게 한다.
- 미설정(null)이면 #492의 글로벌 fallback을 유지(gemini 120s 포함).
- 값이 spawn된 에이전트 프로세스의 env까지 흘러 어댑터가 소비하게 한다(`permission_level` #309와 동일 경로).
- cluster API에서 범위를 검증해 cluster orphan 임계값(1200s)을 건드리지 않는다(N 상한으로 회피).
- 단일 값 N + 자동 보정 멘탈 모델(`turn=N`, `ping=max(N+60,600)`, `supervisor=max(N+300,900)`)을 유지.

## Action

- **cluster** — `db/models.py`에 `turn_timeout_sec: Mapped[int|None]`(nullable) 추가. `db/migrations/versions/049_agent_turn_timeout.py` 신규(down_revision `"048"`, `038` 템플릿). `api/v1/agents.py`: `AgentCreate`/`AgentUpdate`(`turn_timeout_sec`+`_set`)/`AgentOut` 필드, 재사용 검증자 `TurnTimeoutSec = Annotated[Optional[int], AfterValidator(_validate_turn_timeout)]`(하한 30, 상한 `ANYGARDEN_REQUEST_LIVENESS_SEC − 300` 미만), `create_agent`/`update_agent` 배선(`turn_timeout_sec_set` → `runtime_changed=True`). `scheduler/lifecycle.py` spawn payload에 `turn_timeout_sec` 추가.
- **machine** — `protocol/frames.py` `SyncDesiredStateFrame.turn_timeout_sec: int|None`. `daemon.py` `SpawnManifest(turn_timeout_sec=getattr(...))`. `spawner.py` dataclass 필드 + `if msg.turn_timeout_sec is not None: env["ANYGARDEN_AGENT_TURN_TIMEOUT_SEC"] = str(...)`(값 있을 때만 — 미설정 시 어댑터 fallback을 가리지 않도록).
- **agent** — `integrations/_turn_timeout.py` `resolve_turn_timeout` 체인 맨 앞에 per-agent env leg(엔진 무관) 추가, 모듈/함수 docstring 갱신.
- **frontend** — `hooks/useAgents.ts` `Agent` 인터페이스 + `updateAgent` patch 타입. `agent-settings/OverviewPanel.tsx` 메타데이터 그리드에 "Turn timeout" 숫자 입력(blur 커밋, 전용 `turnTimeoutError` 상태, 빈 값=기본 placeholder).
- **tests** — agent `test_turn_timeout.py` per-agent 우선순위; cluster `test_agents_api.py` CRUD·범위 검증·`_set`·generation bump, `test_migrations.py` head `"048"→"049"` 갱신 + `test_049` 라운드트립; machine `test_spawner.py` env 주입/부재; frontend `OverviewPanel.test.tsx` 입력·null 클리어·미변경·검증 에러.

## Decisions

- **env 전파 vs CLI arg**: 어댑터가 타임아웃을 전부 env로 읽으므로(`permission_level`도 env) env 주입이 어댑터 수정을 최소화. CLI arg(`reasoning_effort` 방식)는 agent cli→어댑터 추가 배선이 필요해 기각.
- **값 있을 때만 env 주입**: `permission_level`은 무조건 `"standard"`로 세팅하지만, 턴 타임아웃은 미설정 시 env를 비워 어댑터의 글로벌 fallback(per-engine env / 하드코딩 기본)이 작동하게 해야 한다. 무조건 세팅하면 fallback을 가려버림.
- **재사용 검증자(AfterValidator) vs 핸들러 검증**: `AgentCreate`/`AgentUpdate` 두 모델이 같은 범위 규칙을 쓰므로 `Annotated + AfterValidator`로 단일 함수 공유. 핸들러 검증은 두 엔드포인트에 중복 코드를 만들어 기각.
- **N 상한으로 orphan 회피**: 상한 `N + SUP_SLACK < orphan_threshold`를 cluster API(orphan env를 아는 곳)에서 검증. cluster orphan sweeper를 per-agent로 만들면 sweep 경로 복잡도가 급증해 기각(brainstorming에서 "orphan 미만 제한" 선택).
- **마이그레이션 테스트 head 단언 갱신**: `test_migrations.py`가 head를 `"048"`로 하드코딩(의도된 "새 마이그레이션 시 갱신" 패턴, L46-47 주석). 7개 head 단언을 `"049"`로 올리고 `test_049` 라운드트립을 추가.
- **가정**: orphan 기본 1200s. `ANYGARDEN_REQUEST_LIVENESS_SEC`를 올리면 상한도 자동으로 따라감(검증식이 env 참조). env는 import-time 1회 읽기라 값 변경은 에이전트 재시작 시 반영(UI에 안내).

## Result

- 운영자가 agent settings UI에서 에이전트별 턴 타임아웃(초)을 설정/해제. 값은 DB→spawn env→어댑터로 흘러 #492 헬퍼가 per-agent leg로 해석하며, ping/supervisor가 자동 보정된다. 미설정 에이전트는 글로벌 fallback 유지.
- 범위 검증으로 cluster orphan 임계값을 넘는 값은 422. 마이그레이션 049 up/down 라운드트립 검증.
- 테스트: agent 489 / cluster 1221(049 포함) / machine 375 / frontend OverviewPanel 27. 빌드·타입체크·ruff(변경 파일) 통과.
- 미해결: 없음. 후속으로 "재시작 시 적용" UX 안내는 입력 하단 보조 문구로만 처리(즉시 hot-reload는 비목표).
