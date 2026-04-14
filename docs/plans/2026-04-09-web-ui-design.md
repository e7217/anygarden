# Doorae Web UI 설계

## 개요

doorae-server에 웹 UI를 추가하여 로그인, 채팅, 에이전트/머신 관리를 브라우저에서 수행할 수 있게 한다. `uvx doorae-server` 한 줄로 UI 포함 기동.

## 기술 스택

- **프론트엔드**: React 18 + TypeScript + Vite + shadcn/ui + Tailwind CSS
- **빌드**: `frontend/` → `npm run build` → `doorae/static/`
- **서빙**: FastAPI `StaticFiles` + SPA fallback
- **실시간**: 브라우저 네이티브 WebSocket → 기존 `/ws/rooms/{id}` 핸들러

## 디렉토리 구조

```
doorae-server/
├── frontend/                    # React 소스
│   ├── src/
│   │   ├── components/ui/       # shadcn/ui 컴포넌트
│   │   ├── components/          # 앱 컴포넌트
│   │   │   ├── LoginForm.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── ChatArea.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── MessageInput.tsx
│   │   │   ├── RoomHeader.tsx
│   │   │   ├── AdminAgents.tsx
│   │   │   ├── AdminMachines.tsx
│   │   │   └── CreateRoomDialog.tsx
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useAuth.ts
│   │   │   ├── useRooms.ts
│   │   │   ├── useAgents.ts
│   │   │   └── useMachines.ts
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx
│   │   │   ├── ChatPage.tsx
│   │   │   ├── AdminAgentsPage.tsx
│   │   │   └── AdminMachinesPage.tsx
│   │   ├── lib/
│   │   │   └── api.ts           # fetch wrapper with JWT
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── tailwind.config.ts
├── doorae/
│   ├── static/                  # 빌드 결과물 (git 포함)
│   ├── auth/
│   │   └── routes.py            # NEW: register, login, me
│   └── app.py                   # StaticFiles + SPA fallback 추가
```

## 신규 서버 API

### 인증 (`/api/v1/auth/`)

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/v1/auth/register` | `{email, password}` | `{user_id, token}` |
| POST | `/api/v1/auth/login` | `{email, password}` | `{token, user: {id, email, is_admin}}` |
| GET | `/api/v1/auth/me` | — (Bearer) | `{id, email, is_admin}` |

- 비밀번호: argon2 해시
- 첫 가입자: 자동 admin

### 프로젝트 (`/api/v1/projects/`)

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/v1/projects` | `{name}` | `{id, name}` |
| GET | `/api/v1/projects` | — | `[{id, name, created_at}]` |

## 페이지 구성

### `/login` — 로그인/회원가입
- Card 중앙 배치, 탭으로 로그인/가입 전환
- JWT → localStorage 저장

### `/` — 메인 채팅 (Slack 레이아웃)
- 좌측 사이드바: 프로젝트 → 룸 트리 + 새 룸 생성 버튼
- 중앙: 메시지 목록 (자동 스크롤) + 입력창
- 유저(👤)/에이전트(🤖) 아이콘 구분, typing 표시

### `/admin/agents` — 에이전트 관리
- 테이블: name, engine, state, machine, actions
- 새 에이전트 생성 다이얼로그 (engine 선택, 룸 지정)
- 상태 뱃지: running(green), pending(yellow), crashed(red)

### `/admin/machines` — 머신 관리
- 테이블: name, hostname, status, engines, agents count
- online/offline 뱃지

## WebSocket 연결 흐름

```
로그인 → JWT 획득 → 룸 선택
→ new WebSocket("/ws/rooms/{id}", ["doorae.v1", "bearer.<jwt>"])
→ 수신: onmessage → 채팅 목록 추가 (seq 추적)
→ 전송: ws.send({"type":"send","content":"..."})
→ typing: ws.send({"type":"typing","is_typing":true})
→ 룸 전환: 이전 WS close → 새 WS open (since_seq 복구)
```

## SPA 서빙

```python
# app.py
app.mount("/", StaticFiles(directory=static_dir, html=True))

# /api/* → REST (router prefix로 우선 매칭)
# /ws/*  → WebSocket (router prefix로 우선 매칭)
# /*     → index.html (SPA fallback)
```

## 개발 워크플로

```bash
# 개발 시
cd frontend && npm run dev          # Vite HMR on :5173
cd doorae-server && uv run doorae-server  # API on :8000
# vite.config.ts에서 /api, /ws를 :8000으로 프록시

# 빌드
cd frontend && npm run build        # → ../doorae/static/
```
