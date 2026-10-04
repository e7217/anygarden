# feat(projects): add project executions, unified inbox and per-room agent memory (#778)

- Commit: `c1f05e4` (c1f05e4b8d926f0c3d4c91d408282b2fecbf972e)
- Author: Changyong Um
- Date: 2026-10-05T00:49:08+09:00
- PR: —

## Situation

운영실·서브룸 협업과 자동 운영을 검증하는 QA 수용 시나리오(QA-01~21, 2026-09-30)를 기준으로 구현한 대형 변경이 로컬 작업 트리에만 남아 있었다. 141개 파일이 커밋되지 않은 채 `app.py`에 연결되어 있었고, 기존 제품에는 Room 상하 관계, Task·TaskBlocker, 반복 Goal, durable AgentTurn만 있어 한 번의 프로젝트 목표 실행과 그 상태 전이를 서버가 집행할 수단이 없었다. 또 `Agent.memory_md`가 모든 방의 프롬프트에 주입되어 프로젝트 간 장기 기억이 섞였다.

## Task

- 작업 손실 위험 제거: 미커밋 구현을 기능 브랜치에 보존하고 push
- 운영실 총괄이 서브룸 담당자에게 위임하고, 질문·승인·결과 취합·변경/취소·재시도·한도를 서버 상태 전이로 집행
- 질문·승인·작업을 한 곳에서 보는 통합 인박스
- 장기 기억을 (에이전트, 방) 단위로 격리
- 제약: REST와 MCP에 서로 다른 오케스트레이션 로직을 두지 않고 공용 실행 서비스로 같은 상태 전이를 집행

## Action

- `packages/cluster/anygarden/project_executions/` (신규 22개 파일): 실행 서비스, 위임, 실행 요청(질문)·응답, 승인(`approval_router.py`, 대상·payload digest 고정), 입력 개정과 취소(`mutations.py`, `stop_service.py`), 복구(`recovery.py`), 한도(`limits.py`, `qa_repairs.py`), 사용량(`usage.py`), 인박스 API(`inbox.py`)
- `packages/cluster/anygarden/mcp/project_tools.py`: 에이전트용 실행 도구(`begin_project_execution`, `delegate_project_task`, `request_project_input`, `request_project_approval`, `complete_project_execution` 등)
- `packages/cluster/anygarden/memory/`와 `machine/room_memory.py`, 에이전트 `memory/scope.py`: 방별 메모리, 리비전 확인 쓰기, `session_epoch` 기반 세션 키
- `turns/start_service.py`, 에이전트 `runtime/execution/project_turn.py`·`usage.py`: 실행 범위가 붙은 턴 시작과 Codex 누적 사용량 차분
- DB 모델 3개와 마이그레이션 082–089 (task 의존 결과, run ledger, 프로젝트 실행, 실행 요청, 승인, 방별 메모리, 실행 변경, 네이티브 호출 사용량)
- 프론트엔드: `InboxPage`, 컨텍스트 레일의 `ProjectExecutionsSection`·`ExecutionRequestsSection`·`ExecutionApprovalsSection`, `MemoryPanel`, 관련 훅·i18n 카탈로그, `e2e/inbox.spec.ts`
- `DESIGN.md`: 컨텍스트 레일과 인박스의 정보 배치 규칙 추가
- 설계 문서 6개 (`docs/plans/2026-10-01-*.md`)

## Decisions

- 검토한 선택지 (`docs/plans/2026-10-01-project-room-autonomy-stabilization-design.md`):
  - 기존 Task에 실행 계약을 덧붙이기
  - 별도 프로젝트 실행기 만들기
  - 모델 프롬프트만 강화하기
- 기존 전달·권한·재시도·알림 경로는 재사용하고, 상위 실행 계약과 상태 전이는 서버가 집행하는 방식을 택했다. 프롬프트만으로는 승인, 취소, 반복·비용 한도, 완료 판정을 보장할 수 없기 때문이다.
- 반복 일정(`Goal`)과 한 번의 실행(`ProjectExecution`)을 분리했다. 기존 Goal의 `completed` 표시로는 하위 작업·독립 QA·승인 상태를 검증할 수 없다.
- 장기 기억은 에이전트 전역 `memory_md` 대신 (agent, room) 단위로 저장한다. 프로젝트 격리는 방 격리를 통해 성립시킨다(`docs/plans/2026-10-01-project-memory-isolation-design.md`의 재현 실패 근거).
- 커밋 단위: 141개 파일을 논리 단위로 나누면 오분류 위험이 커서, squash merge를 전제로 단일 커밋으로 보존했다.
- 재검토 조건: 한 방이 여러 프로젝트에 걸치게 되거나, 프로젝트 단위 공유 기억이 요구되면 방 단위 격리 가정을 다시 봐야 한다.

## Result

- 기능 브랜치 `feat/project-room-autonomy`에 보존되어 원격에 push됨. main 머지는 하지 않음.
- 검증은 수동 QA 실행 기록(2026-10-01)뿐이다: QA-01~15, 17~21 통과(일부 범위 한정), QA-16 부분 통과. 이 커밋에서는 테스트를 실행하지 않았다.
- 남은 일(#778): `project_executions`와 인박스 API의 백엔드 pytest(QA-03·06·08·13), 종합 시나리오 실행, main 머지.
