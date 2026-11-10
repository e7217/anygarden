# fix(executions): bind the owner's thread answer to the execution lead turn (#994)

- Commit: `52a7720` (52a7720ac885755b6052e095634eb2976bffb16b)
- Author: Changyong Um
- Date: 2026-11-10T10:35:03+09:00
- PR: #1007

## Situation

실행(execution) 중 워커 태스크가 블록되면 총괄(lead)은 사용자에게 질문을 보내고 대기한다. 사용자의 답변은 아무 실행 바인딩 없이 평범한 turn으로 도착하기 때문에, 총괄이 `resume_project_task`를 호출하면 `EXECUTION_LEAD_REQUIRED` 오류와 함께 거절되었다. 결과적으로 실행이 `waiting_children` 상태에 갇혀 재개할 수 없었다.

## Task

- 사용자가 실행이 시작된 스레드에서 총괄에게 답하는 turn을 해당 실행의 root task에 자동으로 바인딩한다.
- 바인딩이 거절될 조건(실행 비활성·데드라인 초과·한도 초과·다른 스레드·총괄이 아닌 에이전트 등)에서는 평범한 turn으로 폴백한다.
- 총괄에게 보내는 blocked/engine-failure 알림 문구에 "이 스레드에서 묻고 답을 받으라"는 안내를 포함한다.
- EXE-25 요건을 선언하고 pytest로 커버한다.

## Action

**`packages/cluster/anygarden/project_executions/authorization.py`**
- `lead_reply_root_task(db, *, room_id, thread_root_id, participant_id, agent_id)` 함수 추가 (line 141–200).
- 실행이 활성 상태이고, 스레드 루트가 실행의 source message와 일치하며, 총괄이 root task를 담당하고 있을 때만 `root.id`를 반환.
- `ExecutionStop` 비활성 여부, `ExecutionInputRevision` 존재, `admission_disposition` 허용 여부를 순차 검사하여 하나라도 실패하면 `None` 반환.

**`packages/cluster/anygarden/project_executions/service.py`**
- `_ASK_USER_IN_THREAD` 상수 추가 (line 54–57): `"ask the user for what is missing in this thread (when the user answers in this thread and mentions you, call resume_project_task in that turn)"`.
- `reconcile_execution` 내 blocked/engine-failure 안내 문구에 `_ASK_USER_IN_THREAD` 삽입 (line 1811, 1835).

**`packages/cluster/anygarden/ws/handler.py`**
- WS 핸들러의 thread reply 처리 블록에서, `identity.kind == "user"` && `is_thread_reply`일 때 `lead_reply_root_task`를 호출하여 `execution_task_id` 계산 (line 2102–2117).
- `create_turn` 호출에 `task_id=execution_task_id` 추가 (line 2126).

**`packages/cluster/tests/test_execution_thread_reply_binding.py`** (신규, 86줄)
- `test_owner_thread_answer_binds_the_lead_turn_to_the_execution`: WS 경로에서 answer turn의 `task_id`·`execution_id`·`execution_input_revision`이 올바르게 설정되는지 검증.
- peer / 다른-스레드 turn은 바인딩되지 않음을 확인.

**`packages/cluster/tests/test_project_execution_flow.py`**
- `Harness.owner_thread_reply` 헬퍼 추가 (line 282–312): `lead_reply_root_task`를 직접 호출해 integration 흐름에서 사용.
- `test_lead_resumes_blocked_task_from_the_owners_thread_answer` (line 1700–1718): 전체 흐름 검증—블록→총괄 질문→오너 답변 turn 바인딩→`resume_project_task` 성공→워커 완료→실행 완료.
- `test_thread_answer_outside_the_execution_stays_an_ordinary_turn` (line 1720–1757): 다른 스레드·비-총괄 에이전트·비활성 실행에서는 `None` 반환 확인.

**`docs/requirements.md`**
- EXE-25 요건 행 추가.

## Decisions

**바인딩 판단을 별도 함수로 분리 (`authorization.py`):**
- 옵션 A: WS 핸들러 안에 조건 인라인 → 핸들러가 비대해지고 service 테스트에서 재사용 불가.
- 옵션 B: `create_turn` 내부에서 자동 감지 → turn 생성 레이어에 실행 로직이 혼입.
- **선택 B': 별도 async 함수로 분리** — 핸들러와 integration 테스트 harness 양쪽에서 명시적으로 호출 가능하고, 함수 시그니처가 판단 조건을 문서화. `create_turn` 레이어는 오염되지 않음.

**폴백 대신 `None` 반환:**
- 거절 조건에서 예외를 던지면 `create_turn`까지 전파되어 평범한 turn도 실패할 수 있다.
- `None` 반환 시 호출부가 `task_id=None`으로 평범한 turn을 생성하므로, 사용자 메시지는 어떤 경우에도 에이전트에 전달된다.

**안내 문구를 상수로 추출 (`_ASK_USER_IN_THREAD`):**
- blocked 알림과 engine-failure 알림 두 군데에 같은 지시가 들어가므로 중복 제거 목적.
- 문구 변경 시 한 곳만 수정하면 되어 future-proof.

## Result

- EXE-25 요건이 선언되고 `@pytest.mark.req("EXE-25")` 테스트 3개로 커버된다.
- 오너가 실행 스레드에서 총괄을 멘션하면, 그 turn이 root task에 바인딩되어 `resume_project_task`가 거절 없이 성공한다.
- 다른 스레드·비-총괄·비활성 실행에서의 답변은 기존처럼 평범한 turn으로 처리되어 regression 없음.
