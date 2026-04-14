---
id: 1
title: Engine adapters use subprocess instead of SDK import
status: accepted
date: 2026-04-09
---

# 1. Engine Subprocess

## Context

원래 엔진 어댑터들은 각 엔진의 Python SDK를 import하여 사용하도록 설계되었다 (예: `from codex import CodexSession`). 그러나 대부분의 엔진 SDK가 존재하지 않거나 불안정하여 stub으로만 남아있었다.

호스트 머신에는 이미 인증된 CLI 도구(codex, claude-code 등)가 설치되어 있으며, 이를 직접 활용하는 것이 설계 의도("Machine이 호스트의 도구를 사용")와 일치했다.

## Decision

Codex 어댑터를 `codex exec` subprocess 호출 방식으로 재작성한다:
- `asyncio.create_subprocess_exec`으로 `codex exec --ephemeral --skip-git-repo-check` 실행
- 응답은 `-o` 플래그로 파일 출력 → 읽기
- 대화 컨텍스트는 프롬프트에 포함 (룸별 격리)
- 모델 지정 없으면 codex CLI 기본값 사용

다른 엔진(claude-code, openhands, deep-agents)은 아직 stub 상태이며, 동일한 subprocess 패턴으로 전환 예정.

## Consequences

- 호스트에 설치된 도구의 인증 정보를 그대로 사용 (API 키 별도 관리 불필요)
- codex CLI의 버전/동작에 의존 (내부 API 변경 시 깨질 수 있음)
- 프로세스 spawn 오버헤드가 있으나 채팅 응답 시간(수 초) 대비 무시 가능
- 향후 엔진 SDK가 안정화되면 다시 import 방식으로 전환 가능
