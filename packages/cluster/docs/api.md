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
| PATCH | `/agents/{id}` | 에이전트 수정 |
| DELETE | `/agents/{id}` | 에이전트 삭제 |
| POST | `/agents/{id}/spawn` | 에이전트 spawn |
| POST | `/agents/{id}/kill` | 에이전트 kill |
| PUT | `/agents/{id}/files` | 에이전트 파일(manifest) 업데이트 |

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
