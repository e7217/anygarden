# doorae-cluster Architecture

## Overview

doorae-cluster는 다중 에이전트 채팅 서버이다. FastAPI + SQLite + WebSocket 기반으로, 유저와 에이전트가 룸에서 대화하고 머신에 에이전트를 스케줄링한다.

## Package Structure

```
doorae/
├── app.py              # FastAPI 앱 팩토리
├── cli.py              # CLI 엔트리포인트 (doorae-server)
├── config.py           # 설정 (DooraeSettings, pydantic-settings)
├── dependencies.py     # FastAPI 의존성 주입
├── agent_files.py      # 에이전트 파일/manifest 관리
│
├── auth/               # 인증
│   ├── routes.py       # /api/v1/auth (register, login, dev-token, me)
│   ├── dependencies.py # JWT 검증, Identity 추출
│   ├── jwt.py          # JWT 토큰 생성/검증
│   └── password.py     # argon2 패스워드 해싱
│
├── api/v1/             # REST API
│   ├── agents.py       # 에이전트 CRUD, 파일 관리
│   ├── machines.py     # 머신 등록, 상태 조회
│   ├── projects.py     # 프로젝트 관리
│   ├── tasks.py        # 태스크 보드
│   ├── saved.py        # 저장된 메시지
│   └── search.py       # 전문 검색
│
├── ws/                 # WebSocket
│   ├── handler.py      # 유저/에이전트 WebSocket 핸들러
│   └── machine_handler.py  # 머신 데몬 WebSocket 핸들러
│
├── rooms/              # 룸 관리
│   └── router.py       # 룸 CRUD, 서브룸
│
├── messages/           # 메시지
│   └── router.py       # 메시지 전송, 히스토리
│
├── scheduler/          # 스케줄러
│   ├── lifecycle.py    # 에이전트 생명주기 (spawn/kill 결정)
│   └── machine_bus.py  # 머신 연결 관리, spawn 명령 전달
│
├── orchestration/      # 오케스트레이션
│
├── observability/      # 모니터링 (Prometheus 메트릭)
│
├── db/                 # 데이터베이스
│   ├── models.py       # SQLAlchemy 모델
│   └── migrations/     # Alembic 마이그레이션
│
└── static/             # 프론트엔드 빌드 출력

frontend/               # React + Vite + shadcn/ui
├── src/
│   ├── pages/          # 페이지 컴포넌트
│   ├── components/     # UI 컴포넌트
│   └── lib/            # API 클라이언트, 유틸
└── vite.config.ts      # Vite 설정 (API 프록시 포함)
```

## Core Flow

```
브라우저/클라이언트
    │
    ├── REST API (/api/v1/*)     → FastAPI 라우터
    │
    └── WebSocket (/ws/chat)     → handler.py
            │
            ├── 메시지 브로드캐스트 (룸 참여자)
            │
            └── 에이전트 응답 수신/전달

머신 데몬
    │
    └── WebSocket (/ws/machines/{id}) → machine_handler.py
            │
            ├── 하트비트 수신
            ├── spawn/kill 명령 전송
            └── 에이전트 상태 보고 수신

스케줄러
    │
    └── lifecycle.py
            │
            ├── 에이전트 spawn 요청 → 머신 선택 (bin-pack)
            └── spawn 명령 → machine_handler → 머신 데몬
```

## Authentication

3종 토큰 체계:
- **User JWT**: 유저 로그인 시 발급, REST API + WebSocket 인증
- **Agent Token**: 에이전트별 발급, 서버 접속 시 사용
- **Machine Token**: 머신 등록 시 발급, 데몬 WebSocket 인증

## Database

SQLite + aiosqlite (비동기). Alembic으로 마이그레이션 관리.

주요 테이블: users, rooms, machines, agents, agent_tokens, machine_tokens, messages, participants, agent_files, tasks
