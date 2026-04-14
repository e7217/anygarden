# doorae-machine Operations

## 설치 및 실행

### 1. 머신 등록

```bash
uvx doorae-machine register \
  --server http://localhost:8001 \
  --name "my-machine" \
  --max-agents 5
```

서버에 인증 후 `~/.doorae/machine.toml`에 머신 ID와 토큰이 저장된다.

### 2. 데몬 실행

```bash
uvx doorae-machine run --server ws://localhost:8001
```

서버에 WebSocket으로 연결하고, 에이전트 spawn 명령을 대기한다.

### 3. 상태 확인

```bash
uvx doorae-machine status
```

### 4. systemd 서비스 등록 (프로덕션)

```bash
uvx doorae-machine install-systemd-unit
systemctl --user enable doorae-machine
systemctl --user start doorae-machine
```

## 설정 파일

### ~/.doorae/machine.toml

`register` 명령이 자동 생성한다:
- `machine_id`: 서버에서 발급한 머신 ID
- `token`: 인증 토큰
- `server_url`: 서버 주소

### ~/.doorae/agents/

에이전트별 디렉토리가 자동 생성된다:
```
~/.doorae/agents/<agent_id>/
├── AGENTS.md           # 에이전트 지시사항
├── CLAUDE.md           # → AGENTS.md (symlink)
├── skills/             # 스킬 파일
├── .codex/.env         # 엔진별 시크릿
└── workspace/          # 에이전트 작업 디렉토리 (persist)
    └── MEMORY.md       # 세션 간 메모리
```

## 개발 환경

개발 시에는 해당 디렉토리에서 직접 실행:

```bash
cd doorae-machine
uv run doorae-machine run --server ws://localhost:8001
```

로컬 코드 변경이 바로 반영된다.

## 트러블슈팅

### 에이전트가 spawn되지 않음
- `doorae-agent`가 PATH에 있는지 확인 (`which doorae-agent`)
- 없으면 `uvx`로 PyPI에서 가져옴 — 네트워크 연결 확인
- 서버 로그에서 spawn 명령 확인

### 에이전트가 반복 crash
- crash budget 초과 시 자동으로 spawn 중단
- `~/.doorae/agents/<id>/workspace/` 에서 에이전트 로그 확인
- 서버 UI에서 에이전트 상태 확인

### WebSocket 연결 끊김
- 데몬이 자동 재연결 시도
- 서버 주소/포트 확인
- 머신 토큰 유효성 확인 (`doorae-machine status`)
