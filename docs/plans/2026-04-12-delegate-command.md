# `/delegate` 명령 — 메인룸 → 서브룸 작업 위임

**날짜**: 2026-04-12
**상태**: 설계 확정, 구현 전

## 요약

메인 채팅룸에서 에이전트에게 전달된 작업을 기존 서브룸으로 위임하는 명시적 명령.
에이전트가 서브룸에 작업을 전달하고, 서브룸 에이전트의 응답을 메인룸에 보고한다.

## 명령 형식

```
@에이전트이름 /delegate 서브룸이름 작업내용
```

**예시**:
```
@테스트에이전트 /delegate 디자인검토 이 API 응답 스키마를 리뷰해줘
```

## 동작 흐름

```
사용자 (메인룸)
  │  "@테스트에이전트 /delegate 디자인검토 API 스키마 리뷰"
  ▼
테스트에이전트 (메인룸에서 수신)
  │  1. /delegate 파싱 → sub_room_name="디자인검토", task="API 스키마 리뷰"
  │  2. REST: GET /api/v1/rooms/{parent}/sub-rooms?name=디자인검토
  │  3. 서브룸 찾음 → sub_room_id
  │  4. client.send(메인룸, "서브룸 '디자인검토' 에 작업을 전달했습니다")
  │  5. client.send(서브룸, "[DELEGATED] API 스키마 리뷰")
  │  6. 서브룸 WS 에서 첫 번째 타인 응답 대기
  ▼
서브에이전트1 (서브룸에서 수신)
  │  "[DELEGATED] API 스키마 리뷰" → LLM 처리 → 응답
  ▼
테스트에이전트 (서브룸에서 응답 캡처)
  │  7. client.send(메인룸, "서브룸 '디자인검토' 결과:\n{응답내용}")
  ▼
사용자 (메인룸에서 결과 확인)
```

## 에러 케이스

| 상황 | 메인룸 응답 |
|------|------------|
| 서브룸이름이 존재하지 않음 | "서브룸 '디자인검토' 를 찾을 수 없습니다" |
| 에이전트가 서브룸의 참여자가 아님 | "서브룸 '디자인검토' 에 참여하고 있지 않습니다" |
| 서브룸에서 30초 내 응답 없음 | "서브룸 '디자인검토' 에서 응답이 없습니다 (timeout)" |

## 변경 대상

### 서버 (doorae-server)

**`rooms/router.py`** — 서브룸 이름 검색 엔드포인트 추가:
```
GET /api/v1/rooms/{room_id}/sub-rooms?name={name}
```
- parent_room_id = room_id, name = query param 으로 필터
- 200: RoomOut (첫 매치) 또는 404

### SDK (doorae-sdk)

**`client.py`** — REST 헬퍼:
```python
async def find_sub_room(self, parent_room_id: str, name: str) -> str | None:
    """이름으로 서브룸 검색. room_id 반환 또는 None."""
```

**`integrations/base.py`** — delegate 파싱 + 결과 캡처:
```python
@dataclass
class DelegateRequest:
    sub_room_name: str
    task: str

def parse_delegate(content: str) -> DelegateRequest | None:
    """'/delegate 서브룸이름 작업내용' 파싱. 매치 안 되면 None."""
```

**`integrations/codex.py`, `gemini_cli.py`, `claude_code.py`** — `_handle` 수정:
```python
@client.on_message
async def _handle(msg):
    content = msg.get("content", "")
    delegate = parse_delegate(content)
    
    if delegate:
        await execute_delegate(client, msg, delegate)
        return  # LLM 호출 생략
    
    # 기존 로직 (LLM 호출 + 응답)
```

**`integrations/delegate.py`** (신규) — 위임 실행 로직:
```python
async def execute_delegate(client, msg, delegate):
    room_id = msg["room_id"]
    
    # 1. 서브룸 검색
    sub_room_id = await client.find_sub_room(room_id, delegate.sub_room_name)
    if not sub_room_id:
        await client.send(room_id, f"서브룸 '{delegate.sub_room_name}' 를 찾을 수 없습니다")
        return
    
    # 2. 메인룸에 확인
    await client.send(room_id, f"서브룸 '{delegate.sub_room_name}' 에 작업을 전달했습니다")
    
    # 3. 서브룸에 작업 전달
    await client.send(sub_room_id, f"[DELEGATED] {delegate.task}")
    
    # 4. 서브룸에서 첫 번째 타인 응답 캡처 (v1)
    result = await wait_for_sub_room_reply(client, sub_room_id, timeout=30)
    
    # 5. 메인룸에 결과 보고
    if result:
        await client.send(room_id, f"서브룸 '{delegate.sub_room_name}' 결과:\n{result}")
    else:
        await client.send(room_id, f"서브룸 '{delegate.sub_room_name}' 에서 응답이 없습니다 (timeout)")
```

## 결과 캡처 방식 — v1 vs v2 진화 경로

### v1 (이번 구현): 단일 응답 = 완료

- delegate 에이전트가 서브룸 WS 에서 **첫 번째 타인 메시지**를 수신하면 메인룸에 보고
- 서브룸에 에이전트가 여러 개면 **가장 빠른 응답** 이 결과
- 구현 단순: `_process_frame` 에 one-shot callback 등록

### v2 (향후): 명시적 `/done` 종료

**전제**: 서브룸 에이전트 간 멀티턴 대화가 가능해진 후 (Phase X Agent Protocol)

- 서브룸 에이전트가 작업 완료 시 `/done 결과요약` 메시지 전송
- delegate 에이전트가 `/done` 을 감지하면 그 내용을 메인룸에 보고
- 멀티턴 중간 메시지는 무시하고 `/done` 만 캡처

**v1 → v2 마이그레이션 포인트**:
- `wait_for_sub_room_reply()` 함수의 종료 조건만 교체
  - v1: `첫 번째 타인 메시지`
  - v2: `content.startswith("/done")` 인 메시지
- `execute_delegate()` 의 나머지 흐름은 동일
- 어댑터 코드 변경 없음 (delegate.py 만 수정)

## 테스트 계획

1. **파싱 단위 테스트**: `parse_delegate` — 정상 케이스, 서브룸이름 없음, 작업내용 없음
2. **서브룸 검색 테스트**: `GET /rooms/{id}/sub-rooms?name=X` — 존재, 미존재, 빈 이름
3. **위임 통합 테스트**: mock WS 로 delegate 흐름 전체 검증
4. **에러 케이스 테스트**: 서브룸 없음, 참여자 아님, timeout

## 범위 외

- 서브룸 자동 생성 (사전에 만들어둬야 함)
- 멀티턴 대화 오케스트레이션 (v2 / Phase X)
- LLM 이 자발적으로 delegate 판단 (B 방식 — 별도 설계)
