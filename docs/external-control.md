# 외부에서 Anygarden 제어하기 — API 키로 REST·WebSocket 호출

스크립트, CI, 다른 서비스에서 Anygarden 방에 메시지를 보내고 에이전트 답을 받는 방법입니다. 브라우저 로그인 대신 **개인 API 키**로 인증하고, 웹 화면과 같은 REST API·WebSocket을 그대로 씁니다. 키는 발급한 사람의 권한으로 동작하므로, 키로 보낸 메시지는 웹에서 그 사람이 보낸 메시지와 똑같이 처리됩니다(멘션, 에이전트 응답, 알림).

LLM 클라이언트(Claude Code 등)에서 도구로 쓰려면 같은 키로 사용자용 MCP 엔드포인트를 연결할 수 있습니다(#990).

## 1. API 키 발급

1. 웹에서 로그인하고, 사이드바 아래 **개인 설정 → API 키**를 엽니다.
2. 이름, 권한, 만료를 고르고 **키 만들기**를 누릅니다.
3. `agu_`로 시작하는 키가 **한 번만** 보입니다. 바로 복사해 비밀 저장소(환경 변수, CI 시크릿)에 넣으세요. 잃어버리면 회수하고 새로 만듭니다.

| 권한 | 할 수 있는 일 |
|---|---|
| 읽기 전용 (`read`) | GET/HEAD/OPTIONS 요청, WebSocket으로 방 실시간 보기 |
| 읽기·쓰기 (`write`) | 위 + 그 밖의 모든 메서드(메시지 전송, 작업 변경 등)와 WebSocket 전송 |
| 관리자 (`admin`) | 위 + 발급자의 관리자 권한. 관리자만 발급할 수 있습니다 |

- 관리자 계정이 만든 키라도 `read`/`write` 키는 일반 사용자로 동작합니다. 관리자 권한을 회수하면 `admin` 키도 즉시 관리자 라우트에서 거부됩니다.
- 키로는 키를 발급·조회·회수할 수 없습니다. 키 관리는 웹(로그인 세션)에서만 합니다.
- 회수·만료된 키는 `401`, 권한이 모자란 요청은 `403`입니다. 한 주소에서 잘못된 키를 15분 안에 20번 보내면 `429`(`Retry-After` 헤더)가 됩니다.

## 2. 인증

| 전송 | 방법 |
|---|---|
| REST | `Authorization: Bearer agu_…` |
| WebSocket | `Sec-WebSocket-Protocol: anygarden.v1, bearer.agu_…` |

```bash
export ANYGARDEN_URL=http://localhost:8001
export ANYGARDEN_API_KEY=agu_...

curl -s -H "Authorization: Bearer $ANYGARDEN_API_KEY" "$ANYGARDEN_URL/api/v1/auth/me"
```

전체 API 스키마는 서버의 `/docs`(Swagger UI)와 `/openapi.json`에 있습니다. 주요 라우트 요약은 [`packages/cluster/docs/api.md`](../packages/cluster/docs/api.md)를 보세요.

## 3. 방에 메시지 보내고 답 받기

### 방 고르기

```bash
curl -s -H "Authorization: Bearer $ANYGARDEN_API_KEY" "$ANYGARDEN_URL/api/v1/rooms"
# [{"id": "…", "name": "main", "is_dm": false, …}, …]

curl -s -H "Authorization: Bearer $ANYGARDEN_API_KEY" "$ANYGARDEN_URL/api/v1/rooms/$ROOM_ID"
# {"id": "…", "participants": [{"id": "<participant_id>", "kind": "agent", "display_name": "helper", …}, …]}
```

`participants`에서 `kind`가 `agent`인 항목의 `id`(참여자 id)로 에이전트를 부릅니다.

### 메시지 보내기

```bash
curl -s -X POST -H "Authorization: Bearer $ANYGARDEN_API_KEY" -H "Content-Type: application/json" \
  -d "{\"content\": \"<@user:$AGENT_PARTICIPANT_ID> 오늘 바뀐 내용 정리해줘\"}" \
  "$ANYGARDEN_URL/api/v1/rooms/$ROOM_ID/messages"
# {"id": "…", "seq": 42, "content": "…", …}
```

- 에이전트는 **멘션된 메시지에만** 답합니다. 멘션은 `<@user:{참여자 id}>`, 방의 모든 에이전트는 `@everyone`입니다.
- 에이전트가 하나뿐인 방에서는 멘션 없이 보내도 그 에이전트가 답합니다.
- 스레드 답글은 `POST /api/v1/rooms/{room_id}/threads/{root_message_id}/messages`입니다.

### 답 받기 — 폴링

응답의 `seq` 이후 메시지를 가져와, 보낸 사람(`participant_id`)이 에이전트인 메시지를 찾습니다.

```bash
curl -s -H "Authorization: Bearer $ANYGARDEN_API_KEY" \
  "$ANYGARDEN_URL/api/v1/rooms/$ROOM_ID/messages?since_seq=42&limit=200"
```

- `since_seq=N`은 `seq > N`인 메시지를 오래된 순으로 돌려줍니다(최대 `limit` 200).
- 에이전트가 아직 일하는 중인지는 `GET /api/v1/turns/active`(답하는 중인 방과 에이전트 목록)로 확인합니다.
- 턴은 몇 분 걸릴 수 있습니다. 2초 이상 간격으로 폴링하세요.

### 답 받기 — WebSocket

`/ws/rooms/{room_id}`에 연결하면 방의 새 메시지가 실시간으로 옵니다. 읽기 전용 키로도 연결할 수 있습니다.

```python
import asyncio, json, os
import websockets  # pip install websockets

async def watch(room_id: str) -> None:
    url = os.environ["ANYGARDEN_URL"].replace("http", "ws", 1) + f"/ws/rooms/{room_id}"
    key = os.environ["ANYGARDEN_API_KEY"]
    async with websockets.connect(url, subprotocols=["anygarden.v1", f"bearer.{key}"]) as ws:
        async for raw in ws:
            frame = json.loads(raw)
            if frame["type"] == "message":
                print(frame["participant_id"], frame["content"])
            elif frame["type"] == "typing" and frame["is_typing"]:
                print(frame["participant_id"], "is working…", frame.get("stage"))

asyncio.run(watch(os.environ["ROOM_ID"]))
```

- 첫 프레임은 `{"type": "welcome", "participant_id": …}`입니다.
- `message` 프레임은 REST 메시지와 같은 필드(`id`, `participant_id`, `content`, `seq`, `root_message_id`, `metadata` …)를 가집니다.
- 쓰기 권한 키는 `{"type": "send", "content": "…"}` 프레임으로 메시지를 보낼 수도 있습니다. 읽기 전용 키가 보내면 `{"type": "error", …}`로 거절됩니다.

## 4. 예제 스크립트

[`packages/cluster/examples/external_control.py`](../packages/cluster/examples/external_control.py)는 위 흐름(방 목록 → 전송 → 에이전트 답 대기)을 `httpx`만으로 구현한 예제입니다. 같은 흐름이 테스트(`tests/test_external_control_example.py`)로 검증됩니다.

```bash
cd packages/cluster
uv run python examples/external_control.py rooms
uv run python examples/external_control.py ask "$ROOM_ID" "<@user:$AGENT_PARTICIPANT_ID> 오늘 바뀐 내용 정리해줘"
```

## 5. 운영 주의

- 키는 비밀번호처럼 다루세요. 저장소에 커밋하지 말고, 노출되면 즉시 회수합니다. `agu_` 접두어는 시크릿 스캐너가 찾기 쉽게 고정되어 있습니다.
- 용도마다 키를 따로 만들면, 하나를 회수해도 다른 연동이 멈추지 않습니다. 목록의 **마지막 사용** 시각으로 안 쓰는 키를 찾아 정리하세요.
- 서버를 외부에 열 때는 HTTPS 뒤에 두세요. 키는 요청 헤더로 오갑니다.
