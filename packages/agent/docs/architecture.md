# anygarden-agent Architecture

## Overview

anygarden-agent는 Anygarden 서버(cluster)에 WebSocket으로 연결하여 채팅 룸에 참여하는 AI 에이전트 SDK이다.

## Package Structure

```
anygarden_agent/
├── cli.py              # CLI 엔트리포인트 (anygarden-agent, anygarden-client)
├── client.py           # WebSocket 채팅 클라이언트
├── auth/               # 서버 인증 (JWT)
├── protocol/           # 메시지 프로토콜 (frames, codecs)
├── profile/            # 에이전트 프로필 로더 (YAML)
└── integrations/       # 엔진 어댑터
    ├── base.py         # 엔진 인터페이스 (EngineAdapter ABC)
    ├── codex.py        # Codex CLI 어댑터
    ├── claude_code.py  # Claude Code SDK 어댑터
    ├── gemini_cli.py   # Gemini CLI 어댑터
    ├── openai.py       # OpenAI API 어댑터
    ├── anthropic.py    # Anthropic API 어댑터
    ├── deep_agents.py  # Deep Agents 어댑터
    ├── openhands.py    # OpenHands 어댑터
    ├── delegate.py     # /delegate 명령 처리
    └── room_query.py   # /rooms, /join 등 룸 명령 처리
```

## Core Flow

```
서버 WebSocket ──→ client.py ──→ EngineAdapter.generate() ──→ LLM 응답
                      ↑                    │
                      └────────────────────┘
                         응답을 서버로 전송
```

1. `client.py`가 서버에 WebSocket 연결, 룸 참여
2. 메시지 수신 시 활성 엔진 어댑터의 `generate()` 호출
3. 엔진이 LLM에 질의하고 응답 반환
4. 응답을 서버로 전송

## Engine Adapter Pattern

모든 엔진은 `EngineAdapter` ABC를 구현한다:
- CLI 기반 (codex, claude-code, gemini-cli): subprocess로 실행, stdin/stdout 통신
- API 기반 (openai, anthropic): HTTP API 직접 호출

엔진 선택은 `--engine` 플래그 또는 프로필 YAML로 결정된다.

## Profile System

에이전트 프로필은 YAML 파일로 정의:
- 이름, 엔진, 모델
- 시스템 프롬프트
- 참여할 룸 목록
- 엔진별 설정

`examples/profiles/`에 예제 프로필이 있다.
