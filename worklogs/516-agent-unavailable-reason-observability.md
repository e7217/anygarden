# feat(agents): 에이전트 응답불가 사유 관찰성·원클릭 복구 (#516)

- Commit: `a8f28bb` (a8f28bb)
- Author: Changyong Um
- Date: 2026-07-03
- PR: #516

## Situation

에이전트가 desired=running이어도 실제로는 응답할 수 없는 상태(엔진 마이그레이션 후
지원 머신 없음, spawn 실패, crash 미복구, DB engine과 실행 프로세스 engine 불일치,
방 미소속)가 있었는데 사용자·admin 누구도 원인을 알 수 없었다. 특히 `request_start`의
no_machine 분기는 사유·ActivityLog를 남기지 않고 `actual_state="pending"`만 커밋하는
유일한 무음 실패였고, 낡은 placement를 그대로 둬 `_place_orphaned_agents`의
`placed_on_machine_id IS NULL` 필터에서도 누락됐다. 사용자는 메시지를 보내도 응답도
progress 표시도 없이 침묵만 겪었다.

## Task

- 미기동 계열 전체를 하나의 "응답불가 + 사유" 개념으로 구조화(단일 진실원).
- admin(원인 상세 + 원클릭 복구)과 방 참여자(가벼운 인지)에 차등 표면화.
- stderr 등 기술 정보는 admin 전용, 사용자에겐 비노출.
- 기존 스키마/프레임 하위호환(신규 컬럼 nullable, 프로토콜 변경 없음).
- hang 감지·백그라운드 자동 재시도·런타임 엔진 변경 API는 범위 밖.

## Action

- `db/models.py` — Agent에 `unavailable_code`(indexed)/`unavailable_detail`(JSON)/
  `unavailable_since` 3컬럼 + `ix_agents_unavailable_code` 추가.
- `db/migrations/versions/051_agent_unavailable_reason.py` — batch_alter_table로
  3컬럼+인덱스 추가/제거(050→051, backfill 불필요).
- `agent_availability.py` (신규) — 사유 코드 vocabulary(NO_MACHINE_FOR_ENGINE,
  SPAWN_FAILED, ENGINE_MISMATCH, CRASHED, NO_ROOM) + `render_unavailable_message`
  (audience별 admin/user, stderr는 user 미노출) + `room_notice_for_unavailable`.
- `scheduler/lifecycle.py` — `_mark_unavailable`/`_clear_unavailable` 헬퍼 +
  write 경로: no_machine(사유+`placed_on_machine_id=None`+ActivityLog),
  no_room, 성공 배치 시 clear, `handle_report_actual_state`에서 running(engine
  대조로 mismatch 판정 or clear)/crashed(uptime≤0→spawn_failed, else crashed)/
  stopped clear, request_stop clear, absent-from-report stopped clear.
- `api/v1/agents.py` — `UnavailableReasonOut` + `AgentOut.unavailable_reason`,
  `_agent_to_out`에서 admin audience로 파생.
- `ws/handler.py` — 사용자 send 시 응답 기대 에이전트 중 미가용을 골라 broadcast
  후 방 시스템 공지 1회(`_notify_unavailable_responders`, `(agent, since)` debounce).
- frontend `hooks/useAgents.ts` — Agent 타입에 `unavailable_reason`.
- frontend `components/AdminMachines.tsx` — Unplaced 뷰에 사유 배지(첫 줄)+hover
  (전체 admin 메시지). 원클릭 재배치는 기존 Play 버튼 재사용.
- 테스트: `test_agent_availability.py`(파생·공지 문구), `test_agent_unavailable_reason.py`
  (write 경로·AgentOut 노출·반응형 공지 debounce), `test_migrations.py` head 갱신.

## Decisions

- **사유 저장 방식** — 세 안(A: Agent 1급 필드 / B: 읽을 때 파생 / C: 이벤트 중심)
  중 A 선택. B는 spawn stderr(실패 순간 머신에서만 앎)를 담을 수 없고 반응형 공지
  트리거가 어려움, C는 "현재 상태"에 이벤트 재생이 필요. A는 단일 진실원이라 세 표면 +
  원클릭 복구가 가장 깔끔하게 붙음. (설계 문서 §3)
- **사람용 메시지 비저장** — code+detail에서 render로 파생. 향후 i18n·audience 게이팅
  유연성 확보, stderr가 사용자에게 새지 않도록 구조적으로 차단.
- **engine_mismatch를 값싼 piggyback으로** — `AgentActual` 프레임이 이미 `engine`을
  실으므로 프로토콜 변경·새 백그라운드 루프 없이 리포트 처리 시 대조 한 줄로 감지.
- **spawn_failed vs crashed 구분** — 프레임에 exit 유형이 없어 `uptime_seconds≤0`을
  "never really started" 휴리스틱으로 사용.
- **트리거·클리어를 `unavailable_code` 유무로 통일** — engine_mismatch는 running
  상태에서도 성립하므로 `actual_state` 단독 기준이면 놓침. 클리어도 조건 해소 기준으로.
- **공지 debounce는 in-memory(per-worker)** — 클러스터가 단일 WS 워커라 충분하고,
  재시작 시 재-1회 게시는 무해. DB 기반 dedup의 복잡도를 피함.
- **placement 리셋을 no_machine에 포함** — 관찰성뿐 아니라 자동 회복 경로(`IS NULL`
  필터) 편입까지 한 번에 해결(설계 문서 §5).
- 반려: hang 감지(능동 헬스 프로브 부재로 별도 설계 필요), 백그라운드 자동 재시도,
  런타임 엔진 in-place 변경 API — 모두 이번 범위 밖(설계 문서 §12).

## Result

- 백엔드: cluster 전체 1247 테스트 통과(신규 22 포함), 변경 파일 ruff clean,
  마이그레이션 050→051 up/down 대칭 확인.
- 프론트: `npm run build`(tsc+vite) 통과.
- no_machine 에이전트는 이제 placement가 풀려 Unplaced 뷰에 사유+원클릭 재배치로 노출.
- 사용자가 응답불가 에이전트에 메시지를 보내면 방에 1회 시스템 공지가 뜬다.
- 후속(이번 범위 밖, PR 본문에 명시): 사이드바 상시 배지 + WS coarse `available`
  프레임, 원클릭 재시작 성공/실패 토스트, machine daemon의 초기 spawn 실패 stderr
  전파(현재 crashed 리포트의 last_crash_reason은 이미 반영됨).
