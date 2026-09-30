# 머신 데몬 운영 (`anygarden machine`)

## 설치 및 실행

머신 데몬은 `anygarden` 배포 패키지의 `machine` extra로 설치한다(#754 이전에는
별도 `anygarden-machine` 패키지였다). 서버 스택(FastAPI/SQLAlchemy)은 설치되지 않는다.

```bash
pip install "anygarden[machine]"
# 또는 전용 venv + systemd 유닛까지 한 번에: packages/cluster/scripts/install-machine.sh
```

`install-machine.sh`로 설치하면 venv의 `bin`이 PATH에 없으므로
`~/.local/bin/anygarden-machine` 런처(내부에서 `anygarden machine` 실행)를 쓴다.

### 1. 머신 등록

```bash
anygarden machine register \
  --server http://localhost:8001 \
  --name "my-machine"
```

서버에 인증 후 `~/.anygarden/machine.toml`에 머신 ID와 토큰이 저장된다.

### 2. 데몬 실행

```bash
anygarden machine run --server ws://localhost:8001
```

서버에 WebSocket으로 연결하고, 에이전트 spawn 명령을 대기한다.

### 3. 상태 확인

```bash
anygarden machine status
```

### 4. systemd 서비스 등록 (프로덕션)

```bash
anygarden machine install-systemd-unit
systemctl --user enable anygarden-machine   # 유닛 이름은 그대로 유지
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

개발 시에는 레포 루트에서 워크스페이스를 동기화한 뒤 실행:

```bash
make install   # uv sync --all-packages --all-extras
uv run anygarden machine run --server ws://localhost:8001
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
- 머신 토큰 유효성 확인 (`anygarden machine status`)

### Connect a machine created in the web app

Deploy the server and the remote daemon from the same `anygarden` release; the
daemon ships inside it (`anygarden[machine]`) since #754. For development,
install this checkout with `python -m pip install -e "packages/cluster[machine]"`
from the repository root.

If the administrator has already created a machine in the web app, connect that
existing identity instead of registering another machine:

```bash
anygarden machine connect --server https://garden.example.com --machine-id MACHINE_ID
# Paste the web-issued machine token at the hidden prompt.
anygarden machine run
```

`connect` saves `~/.anygarden/machine.toml` and `~/.anygarden/machine.token` with
private file permissions. It never sends the token as a command-line argument or
prints it. The command does not contact the server or create a second machine.
`run` uses those saved settings after a restart; use the web guide's connection
check to verify authentication and reachability. One machine identity is saved
per operating-system account. Replacing a different saved identity requires an
explicit `connect --replace`; stop its daemon/service first.

If installed in a virtual environment, activate that environment again in a new
terminal before running `anygarden machine run`. For optional Linux startup,
stop the foreground daemon before enabling its service:

```bash
anygarden machine install-systemd-unit
systemctl --user daemon-reload
systemctl --user enable --now anygarden-machine   # the unit keeps its name
```

### Workspace registry selection

The released implementation currently does **not** advertise external workspace
root enforcement or audit support, for either the remote daemon or integrated
node. Registering a folder does not enable access. The UI reports this limit and
continues to show existing approvals and revocation; it offers new requests only
when the server confirms support. The server supports only restricted Codex read
access with a compatible implementation, and rejects external writes.

For implementations that support the required execution controls, register a
canonical Git repository root with at least one commit and no symlink traversal.
The machine-local registry stores paths; the server receives opaque IDs and
labels. Registration and consent must use the **same** registry as the daemon:

```bash
# Remote daemon: ~/.anygarden/workspaces.json
anygarden machine workspace register /path/to/repository --label project --max-mode read --allow src
anygarden machine workspace list

# Integrated node: use the exact --data-dir of the running `anygarden start`.
anygarden machine workspace --node-data-dir /path/to/node-data register /path/to/repository --label project --max-mode read --allow src
anygarden machine workspace --node-data-dir /path/to/node-data list
```

The integrated commands use `/path/to/node-data/workspace-registry.json`.
Consent commands also require that same `--node-data-dir`; the admin UI uses the
running node's actual directory. Restart the corresponding daemon/node after
registration so its catalog is published. Room approval, system approval, and
short-lived machine-local consent remain separate checks.
