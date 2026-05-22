# anygarden-machine Operations

## 설치 및 실행

### 1. 머신 등록

```bash
uvx anygarden-machine register \
  --server http://localhost:8001 \
  --name "my-machine"
```

서버에 인증 후 `~/.anygarden/machine.toml`에 머신 ID와 토큰이 저장된다.

### 2. 데몬 실행

```bash
uvx anygarden-machine run --server ws://localhost:8001
```

서버에 WebSocket으로 연결하고, 에이전트 spawn 명령을 대기한다.

### 3. 상태 확인

```bash
uvx anygarden-machine status
```

### 4. systemd 서비스 등록 (프로덕션)

```bash
uvx anygarden-machine install-systemd-unit
systemctl --user enable anygarden-machine
systemctl --user start anygarden-machine
```

## 설정 파일

### ~/.anygarden/machine.toml

`register` 명령이 자동 생성한다:
- `machine_id`: 서버에서 발급한 머신 ID
- `token`: 인증 토큰
- `server_url`: 서버 주소

### ~/.anygarden/agents/

에이전트별 디렉토리가 자동 생성된다:
```
~/.anygarden/agents/<agent_id>/
├── AGENTS.md           # 에이전트 지시사항
├── CLAUDE.md           # → AGENTS.md (symlink)
├── skills/             # agent-owned 스킬 파일 (respawn 시 보존)
├── .claude/            # claude-code project settings
├── .codex/             # codex config overlay when present
├── .gemini/            # gemini-cli settings
├── memory/
│   ├── notes.md        # 세션 간 메모리
│   ├── shared/         # 룸 공유 파일
│   └── outbox/         # 에이전트 → 룸 산출물
├── MEMORY.md           # 첫 세션 seed / legacy compatibility
└── workspace/          # codex sandbox fallback only
```

일반 엔진의 subprocess cwd는 `<agent_id>/` 자체다. `workspace/`는
현재 codex 표준 샌드박스가 managed 파일 read-only 예외를 지원하지
않는 버전에서만 생성되는 내부 fallback이며, manifest 업로드 대상이 아니다.
Codex fallback 안에는 `skills -> ../skills` bridge가 있어 표준 권한
Codex도 canonical skill 파일을 직접 개선할 수 있다.

`AGENTS.md`, `CLAUDE.md`, MCP/engine config는 materializer가 매 spawn
복구하는 control-plane 파일이다. 반대로 `skills/`와 `memory/`는 agent
runtime 영역으로 보고 normal respawn에서 agent 수정분을 보존한다.

## 개발 환경

개발 시에는 해당 디렉토리에서 직접 실행:

```bash
cd anygarden-machine
uv run anygarden-machine run --server ws://localhost:8001
```

로컬 코드 변경이 바로 반영된다.

## 트러블슈팅

### 에이전트가 spawn되지 않음
- `anygarden-agent`가 PATH에 있는지 확인 (`which anygarden-agent`)
- 없으면 `uvx`로 PyPI에서 가져옴 — 네트워크 연결 확인
- 서버 로그에서 spawn 명령 확인

### 에이전트가 반복 crash
- crash budget 초과 시 자동으로 spawn 중단
- `~/.anygarden/agents/<id>/` 에서 에이전트 런타임 파일 확인
- 서버 UI에서 에이전트 상태 확인

### WebSocket 연결 끊김
- 데몬이 자동 재연결 시도
- 서버 주소/포트 확인
- 머신 토큰 유효성 확인 (`anygarden-machine status`)
