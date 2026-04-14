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
| POST | `/machines/register` | 머신 등록 |
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
