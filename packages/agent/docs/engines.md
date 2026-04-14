# Engine Adapters

## 지원 엔진

| 엔진 | 타입 | CLI/API | 설명 |
|------|------|---------|------|
| codex | CLI subprocess | `codex exec` | OpenAI Codex CLI |
| claude-code | SDK | Claude Agent SDK | Anthropic Claude Code |
| gemini-cli | CLI subprocess | `gemini` | Google Gemini CLI |
| openai | API | OpenAI Python SDK | GPT 모델 직접 호출 |
| anthropic | API | Anthropic Python SDK | Claude 모델 직접 호출 |
| deep-agents | SDK | LangChain Deep Agents | LangGraph 기반 에이전트 |
| openhands | CLI subprocess | OpenHands CLI | OpenHands 에이전트 |

## CLI 기반 엔진

CLI 엔진은 호스트에 설치된 도구를 subprocess로 실행한다 (ADR-001 참조).

장점:
- 호스트의 인증 정보 그대로 사용
- SDK 불안정/부재 문제 회피
- 엔진 업데이트가 독립적

동작 방식:
1. `asyncio.create_subprocess_exec`로 CLI 프로세스 생성
2. 대화 히스토리를 stdin/인자로 전달
3. stdout에서 응답 수집
4. 프로세스 종료 후 응답 반환

## API 기반 엔진

API 엔진은 Python SDK를 직접 import하여 HTTP API를 호출한다.

동작 방식:
1. SDK 클라이언트 초기화 (API 키는 환경변수)
2. 대화 히스토리를 API 포맷으로 변환
3. streaming 또는 batch로 응답 수신
4. 응답 텍스트 반환

## 엔진 추가 방법

1. `integrations/` 디렉토리에 새 파일 생성
2. `EngineAdapter` ABC 구현 (`generate` 메서드)
3. `integrations/__init__.py`에 등록
4. CLI `--engine` 선택지에 추가
