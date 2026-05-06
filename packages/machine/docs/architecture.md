# doorae-machine Architecture

## Overview

doorae-machine은 호스트 머신에서 실행되는 데몬으로, Doorae 서버(cluster)에 WebSocket으로 연결하여 에이전트 서브프로세스의 생명주기를 관리한다.

## Package Structure

```
doorae_machine/
├── cli.py              # CLI 엔트리포인트 (register, run, status, install-systemd-unit)
├── config.py           # 설정 로더 (~/.doorae/machine.toml)
├── daemon.py           # 메인 데몬 루프 (WebSocket 연결, 하트비트)
├── spawner.py          # 에이전트 subprocess spawn/kill/watch
├── supervisor.py       # 프로세스 감시 (crash 감지, stderr 수집)
├── crash_budget.py     # 크래시 예산 관리 (재시작 제한)
├── agent_dir.py        # 에이전트 디렉토리 경로 검증/보안
├── detector.py         # 호스트 엔진 자동 감지 (codex, claude-code 등)
├── manifest_store.py   # 서버 manifest 로컬 캐시
└── protocol/           # 서버 통신 프레임 (spawn, kill, heartbeat 등)
```

## Core Flow

```
서버 (cluster)
    │
    │ WebSocket
    ▼
daemon.py ──→ spawn 명령 수신
    │
    ▼
spawner.py ──→ agent directory 구성 (materialize)
    │            ├── AGENTS.md
    │            ├── CLAUDE.md → AGENTS.md (symlink)
    │            ├── skills/
    │            ├── .claude/ .codex/ .gemini/
    │            ├── memory/
    │            └── workspace/ (codex sandbox fallback only)
    │
    ▼
subprocess ──→ doorae-agent 실행
    │
    ▼
supervisor.py ──→ 프로세스 감시, crash 보고
```

## Key Components

### Spawner (spawner.py)

에이전트 서브프로세스 관리의 핵심:
1. **materialize**: 서버 manifest를 디스크에 구현 (ADR-002)
   - 에이전트별 디렉토리 `~/.doorae/agents/<agent_id>/`
   - AGENTS.md, 엔진별 설정, memory 디렉토리 작성
   - `skills/`는 agent-owned 디렉토리로 보존하고 manifest skill은 없을 때만 seed
   - `doorae-agent` subprocess cwd는 agent root
   - codex는 `read_only_paths` 미지원 버전 보호를 위해 SDK thread cwd만 `workspace/` fallback 사용
2. **spawn**: `doorae-agent` 또는 `uvx doorae-agent`로 subprocess 시작
3. **kill**: SIGTERM → 10초 대기 → SIGKILL 순서로 종료

### Supervisor (supervisor.py)

subprocess의 exit를 감시하고:
- 정상 종료 → `on_stopped` 콜백
- 비정상 종료 → stderr 수집 후 `on_crashed` 콜백

### Daemon (daemon.py)

서버 WebSocket 연결을 유지하며:
- 하트비트 전송 (실행 중인 에이전트 목록 포함)
- spawn/kill 명령 수신 및 처리
- 연결 끊김 시 재연결

### Detector (detector.py)

호스트에 설치된 엔진 CLI를 자동 감지하여 서버에 보고.
