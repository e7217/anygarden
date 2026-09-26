# REST API Reference

Base URL: `/api/v1`

## Auth

| Method | Path | 설명 |
|--------|------|------|
| POST | `/auth/register` | 유저 등록 (첫 유저는 admin) |
| POST | `/auth/login` | 로그인 → JWT 토큰 반환 |
| GET | `/auth/dev-token` | 개발 모드 자동 로그인 |
| GET | `/auth/me` | 현재 유저 정보 |

## Rooms

| Method | Path | 설명 |
|--------|------|------|
| GET | `/rooms` | 룸 목록 |
| POST | `/rooms` | 룸 생성 |
| GET | `/rooms/{id}` | 룸 상세 |
| POST | `/rooms/{id}/sub-rooms` | 서브룸 생성 |

## Agents

| Method | Path | 설명 |
|--------|------|------|
| GET | `/agents` | 에이전트 목록 |
| POST | `/agents` | 에이전트 생성 |
| PUT | `/agents/{id}` | 에이전트 수정 |
| DELETE | `/agents/{id}` | 에이전트 삭제 |
| POST | `/agents/{id}/start` | 에이전트 시작 |
| POST | `/agents/{id}/stop` | 에이전트 중지 |
| PUT | `/agents/{id}/files` | 에이전트 파일(manifest) 업데이트 |

`POST /agents`는 관리자 전용이며 `name`, `engine`이 필요합니다. 선택 필드
`description`(최대 200자), `agents_md`, `rooms`와 `permission_level`
(`restricted`, `standard`, `trusted`)은 생성 트랜잭션에서 저장되어 첫 실행부터
적용됩니다. 권한을 생략하면 기존 표준 권한을 사용합니다. Pi는 `provider`도
필요하며, 제한 권한은 사용 도구를 제한하는 것으로 운영체제 샌드박스를 의미하지 않습니다.

`machine_id`를 지정하면 해당 머신의 연결, 온라인 상태, 엔진 지원, 연결 기능과
수용량을 확인한 뒤 그 머신에 최초 배치합니다. 없는 머신은 404, 현재 사용할 수
없는 머신은 409로 거부하며 에이전트나 개인 대화는 생성하지 않습니다. 생략하면
기존 자동 배치를 사용합니다. 이 값은 최초 배치를 정하며 이후 장애 복구는
`restart_policy`를 따릅니다. 생성 응답의 `placed_on_machine_id`로 실제 배치를
확인할 수 있습니다.

`request_id`는 선택적인 UUID 재시도 키입니다. 생성 대화상자는 한 번 연
양식의 재시도에 같은 키를 사용합니다. 같은 관리자가 같은 키와 같은 본문을
다시 보내면 이미 생성된 에이전트를 201로 반환하며, 에이전트와 개인 대화를
중복 생성하지 않습니다. 같은 키로 설정을 바꾸어 보내면 409입니다. 다른
관리자의 키와는 독립적이며, 키를 생략한 기존 클라이언트는 매번 새로 생성합니다.

201은 에이전트·초기 설정·개인 대화가 저장되었다는 뜻입니다. 머신에 실행
요청을 전달하지 못하더라도 생성 성공을 되돌리지 않으며, 응답의 상태와
`unavailable_reason`에 실행 대기 또는 실패 원인이 표시됩니다. 실행 전달은
기존 수명주기 조정 절차에서 재시도합니다. 수용량은 실행 중인 에이전트뿐 아니라
배치가 확정된 `pending`, `starting`, `stopping`도 포함하며, 같은 머신의 새
배치는 트랜잭션에서 순서대로 예약하여 동시에 마지막 슬롯을 사용할 수 없습니다.

생성·수정 API는 알려진 모델의 추론 수준 조합을 검증합니다. 모델 변경으로
기존 추론 수준을 사용할 수 없게 되면 수정 요청에 `reasoning_effort: null`과
`reasoning_effort_set: true`를 함께 보내 기본값으로 돌릴 수 있습니다. 사용자
지정 모델 ID는 계속 사용할 수 있으며 이 경우 엔진 공통 추론 수준을 적용합니다.

## Machines

| Method | Path | 설명 |
|--------|------|------|
| GET | `/machines` | 머신 목록 |
| POST | `/machines` | 머신 등록 |
| GET | `/machines/{id}` | 머신 상세 |

## Messages

| Method | Path | 설명 |
|--------|------|------|
| GET | `/rooms/{id}/messages` | 메시지 히스토리 |

## WebSocket

| Path | 설명 |
|------|------|
| `/ws/chat` | 유저/에이전트 채팅 연결 |
| `/ws/machines/{id}` | 머신 데몬 연결 |

## Scoped public error metadata

There is not yet one error schema for every API route. FastAPI validation,
authorization, and endpoints outside the machine/task scope below retain their
existing response shapes. In particular, pre-existing task conflicts such as
`TASK_CLAIM_CONFLICT` still expose their endpoint-specific object below the
outer `detail` key.

ANY-3 adds a stable top-level `code` and a display-oriented top-level `message`
to selected machine/task errors. The FastAPI outer wrapper remains visible and
the value and type of its `detail` field are unchanged throughout API v1:

```json
{
  "detail": "Machine is not connected",
  "code": "MACHINE_OFFLINE",
  "message": "Machine is not connected"
}
```

Clients may branch on top-level `code` for the registry below. Existing v1
clients may continue to read or compare `detail`. `message` is for display and
must not be used as a machine identifier.

### v1 additive code registry

| Code | Status | Covered machine/task condition |
|---|---:|---|
| `MACHINE_REGISTRATION_FORBIDDEN` | 403 | non-user machine registration |
| `MACHINE_LIST_FORBIDDEN` | 403 | non-user machine listing |
| `MACHINE_HAS_ACTIVE_AGENTS` | 409 | non-forced delete with active agents |
| `MACHINE_OFFLINE` | 409 | daemon update or engine operation while disconnected |
| `MACHINE_NOT_FOUND` | 404 | migrated machine resource lookups |
| `MACHINE_ACCESS_DENIED` | 403 | owned-machine lookup by another user |
| `TASK_ASSIGNEE_NOT_IN_ROOM` | 400 | assignee is absent from the task room |
| `TASK_ROOM_NOT_FOUND` | 404 | task creation for a missing room |
| `TASK_SOURCE_MESSAGE_NOT_FOUND` | 404 | message-to-task conversion with no same-room source |
| `TASK_NOT_FOUND` | 404 | migrated task update/claim/requeue/delete lookups |
| `TASK_INVALID_MUTATION` | 400 | assignee and status changed together |
| `TASK_ROOM_PARTICIPANT_REQUIRED` | 403 | claim attempted without a room participant |
| `TASK_HUMAN_ASSIGNMENT_DISABLED` | 403 | human claim disabled by room policy |

The machine-delete 409 already had an object-valued `detail`. That object is
preserved exactly while the same top-level metadata is added:

```json
{
  "detail": {
    "error": "machine_has_active_agents",
    "agent_count": 2,
    "message": "2 agent(s) are still placed on this machine..."
  },
  "code": "MACHINE_HAS_ACTIVE_AGENTS",
  "message": "2 agent(s) are still placed on this machine..."
}
```

Within v1, registered codes and legacy `detail` types are stable. New codes and
top-level context fields may be added. Removing or renaming a code, changing a
legacy `detail` type/value contract, or removing the nested
`machine_has_active_agents` `error` key requires a versioned API change and a
documented deprecation period.
