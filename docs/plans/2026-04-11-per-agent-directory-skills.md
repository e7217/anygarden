---
title: Per-agent directory layout with file-based skills
date: 2026-04-11
status: Phase 0 + 1 + 1.5 + 3 complete (merged to main via PR #3, #4) — Phase 2 (Deep Agents) next
---

# Per-agent directory layout with file-based skills

## 배경

현재 각 에이전트는 `system_prompt` 하나로만 개성을 부여받고, 스킬/툴 구성은 엔진 어댑터 코드에 하드코딩되어 있다. 이 상태로는:

- 같은 역할의 에이전트를 여러 개 만들 수 없다 — 프로필 YAML 한 파일에 모든 지시가 들어가고, 그 이상의 문맥(참조 문서, 스킬 라이브러리, 엔진별 설정)을 실어 보낼 자리가 없다.
- `mcp_servers` 필드는 스키마에만 있고 실제로는 어떤 어댑터도 읽지 않는다 (dead field).
- Codex 의 sandbox 설정, Claude Code 의 skill 디렉토리, Gemini CLI 의 `.gemini/settings.json` 같은 엔진 네이티브 확장 포인트를 전혀 활용하지 않는다.

에이전트마다 `AGENTS.md` + `skills/<name>/SKILL.md` 를 담는 디렉토리를 주면, 파일 기반 discovery 를 네이티브 지원하는 엔진은 0 코드로 즉시 이득을 보고, API 기반 엔진은 얇은 loader 만 추가하면 된다.

## 목표

1. 에이전트마다 머신 로컬 파일시스템에 고정된 디렉토리(`~/.anygarden/agents/<agent_id>/`) 를 배치한다.
2. 디렉토리 내용은 **서버가 source of truth** — admin 이 API/UI 로 편집한 값이 DB 에 저장되고, spawn 프레임으로 머신에 전달되어 로컬 파일로 풀린다.
3. **하나의 포맷을 여러 엔진이 자동 인식하도록** 한다. agents.md (AGENTS.md) 와 agentskills.io (SKILL.md) 표준을 채택한다.
4. subprocess 기동 시 `cwd=<agent_dir>/workspace/` 로 넘겨, Codex / Claude Code / Gemini CLI 의 cwd 상향 탐색이 AGENTS.md 와 엔진 네이티브 config 를 자동 로드하게 한다.
5. Deep Agents 처럼 in-process 라이브러리는 `FilesystemBackend(root_dir=agent_dir)` 로 동일한 레이아웃을 소비한다.

비목표:
- 원격 머신 간 파일 동기화 프로토콜 — spawn 프레임에 인라인 전달로 시작. 대용량은 이후 phase.
- Skill marketplace / versioning — 파일 CRUD 만.

## 엔진별 호환성 분석

2026-04-11 시점의 공식 문서/소스를 기준으로 조사했다. 전체 리포트는 세션 로그 참조.

| 엔진 | 적용 난이도 | 파일 discovery | 비고 |
|---|---|---|---|
| **Codex CLI** | ★☆☆☆☆ | AGENTS.md 상향 탐색 (cwd → git root), 32 KiB 제한. `.codex/config.toml` 에 MCP 서버 선언 가능 | spawner 가 `cwd=workspace/` 로 기동하고 부모에 AGENTS.md + `.codex/config.toml` 만 놓으면 됨 |
| **Gemini CLI** | ★☆☆☆☆ | `.agents/skills/` 를 agentskills.io 표준으로 네이티브 인식 (우선순위 1). `context.fileName=AGENTS.md` 설정으로 AGENTS.md 직접 로드. `.gemini/settings.json` 에 MCP + 모델 + sandbox. `.gemini/.env` 로 API 키 격리 | 구조적으로 Codex 와 쌍둥이. subprocess 어댑터 동일 |
| **Deep Agents** (langchain-ai/deepagents v0.5.2) | ★★☆☆☆ | `create_deep_agent(backend=FilesystemBackend(root_dir=..., virtual_mode=True), skills=["/skills/"], memory=["/AGENTS.md"])` — 한 줄로 붙음. SKILL.md frontmatter (name, description, allowed-tools, metadata) 호환 | 현재 어댑터는 스텁. 재작성 필요. `virtual_mode=True` 필수 (path escape 방지) |
| **Claude Code SDK** | ★★☆☆☆ | `ClaudeAgentOptions(cwd=..., setting_sources=["project"])` 가 **필수** — 이 플래그 없으면 CLAUDE.md 안 읽음. `.claude/skills/<name>/SKILL.md` 자동 탐색. `.mcp.json` 또는 `.claude/settings.json` 의 `mcpServers` | 현재 어댑터는 conceptual 스텁. 재작성 + `setting_sources` 지정 필수 |
| **OpenHands** (V1 Software Agent SDK) | ★★★★☆ | `.openhands/microagents/*.md` (YAML frontmatter + triggers). `Conversation(agent=..., workspace=<path>)` 로 workspace 지정. AGENTS.md 는 **자동 로드 안 함** — 어댑터가 직접 파싱해 system prompt 주입 | V0 TOML 설정은 2026-04-01 자로 제거됨. 기존 어댑터는 통째로 V1 재작성 필요 |
| ~~Anthropic raw SDK~~ | — | 파일 discovery 없음. Managed Agents API 는 skill 업로드 방식으로만 지원 | **Phase 0 범위에서 제외** (deferred by 2026-04-11 결정) |
| ~~OpenAI raw SDK~~ | — | 파일 discovery 없음. openai-agents 패키지 + function tool 로 `get_skill(name)` 노출해야 | **Phase 0 범위에서 제외** (deferred by 2026-04-11 결정) |

## 디렉토리 레이아웃

에이전트 루트 (머신 로컬):

```
~/.anygarden/agents/<agent_id>/
├── AGENTS.md                    ← source of truth (agents.md 표준)
├── CLAUDE.md    → AGENTS.md     ← Claude Code 자동 탐색
├── skills/
│   └── <skill-name>/
│       └── SKILL.md             ← agentskills.io 표준
├── .agents/
│   └── skills/   → ../skills    ← Gemini CLI 네이티브 경로 (표준)
├── .gemini/
│   ├── settings.json            ← MCP + model + context.fileName=AGENTS.md
│   └── .env                     ← GEMINI_API_KEY 격리
├── .claude/
│   ├── settings.json
│   └── skills/   → ../skills
├── .codex/
│   └── config.toml              ← MCP + sandbox 기본
├── .openhands/
│   └── microagents/             ← SKILL.md 에서 생성 또는 symlink (phase 4)
└── workspace/                   ← subprocess cwd
```

핵심 원칙:
- **AGENTS.md 1파일 + `skills/<name>/SKILL.md` 1디렉토리** 가 단일 source of truth
- 엔진별 관례 파일명(`CLAUDE.md`, `GEMINI.md`) 은 모두 심볼릭 링크
- 엔진별 config (`.gemini/settings.json`, `.codex/config.toml`, `.claude/settings.json`) 는 spawner 가 프로필로부터 렌더링
- subprocess 는 언제나 `cwd=workspace/` 로 기동 → 부모 탐색으로 AGENTS.md 발견
- `workspace/` 는 에이전트가 쓸 스크래치 디렉토리 (비어있는 상태로 시작)

## 서버 DB 스키마

현재:

```
agents
  id, name, engine, desired_state, actual_state, ...
  profile_yaml: Text  -- AgentProfile YAML
```

확장:

```
agents
  (기존 유지)
  agents_md: Text                      -- AGENTS.md 본문

agent_files (신규)
  id          UUID PK
  agent_id    UUID FK → agents.id (ON DELETE CASCADE)
  path        VARCHAR(512)             -- 루트 상대 경로, e.g. "skills/coder/SKILL.md"
  content     Text
  updated_at  DATETIME
  UNIQUE(agent_id, path)
```

경로 제약 (서버 validation + 머신 validation 양쪽):
- 선행 `/` 금지, `..` 금지, 심볼릭 링크 표현 금지, 절대경로 금지
- 허용 prefix whitelist: `skills/`, `.codex/`, `.claude/`, `.gemini/`, `.openhands/`, 확장자 `.md` / `.json` / `.toml` / `.txt`
- 그 외는 거부 (실행 파일 생성 방지)

## spawn_agent 프레임 확장

현재 (`anygarden-server/anygarden/scheduler/lifecycle.py:72-81`):

```json
{
  "type": "spawn_agent",
  "agent_id": "...",
  "engine": "...",
  "agent_token": "...",
  "profile_yaml": "...",
  "rooms": [...],
  "server_url": "...",
  "name": "..."
}
```

확장:

```json
{
  ...기존...,
  "agents_md": "...",            // AGENTS.md 본문
  "files": {                     // 에이전트 디렉토리 트리 (상대경로 → 내용)
    "skills/coder/SKILL.md": "---\nname: coder\ndescription: ...\n---\n...",
    ".codex/config.toml": "[mcp_servers.docs]\ncommand = \"...\"\n",
    ".gemini/settings.json": "{\"mcpServers\": {...}, \"context\": {\"fileName\": \"AGENTS.md\"}}"
  },
  "engine_secrets": {            // 선택. 각 엔진의 .env 로 렌더링
    "GEMINI_API_KEY": "...",
    "ANTHROPIC_API_KEY": "..."
  }
}
```

머신 데몬의 `Spawner.spawn` 은 새로 추가되는 `_materialize_agent_dir(msg)` 헬퍼로 **declarative reconcile** 를 수행한다. 핵심 규칙:

> 매 spawn 시 `workspace/` 를 **제외한** 모든 managed 트리를 manifest 로부터 재구성한다. manifest 에 없는 파일/디렉토리/심볼릭 링크는 삭제된다.

이 규칙이 필요한 이유: admin 이 DB 의 `agent_files` 에서 한 행을 삭제해도 (예: 스킬 제거, MCP 서버 제거), spawn 프레임의 `files` 맵에는 그 경로가 단순히 빠져 있을 뿐이다. 머신이 기존 파일을 능동적으로 지우지 않으면 엔진의 cwd 상향 탐색이 삭제된 파일을 계속 인식해서 **manifest 에서의 삭제가 실제 동작에 반영되지 않는 유령 상태** 가 된다. Declarative reconcile 은 이 이슈를 원천 차단한다.

구체 알고리즘:

1. **경로 결정**: `agent_root = ~/.anygarden/agents/<agent_id>/`. 없으면 `mkdir -p` 로 생성.
2. **Prune (managed 트리 정리)**: `agent_root` 를 순회하되 `workspace/` 하위는 건드리지 않는다. 그 외의 모든 파일, 디렉토리, 심볼릭 링크를 삭제한다 (`shutil.rmtree` 또는 재귀 unlink). 주의:
   - `workspace/` 디렉토리 **자체** 는 유지 (존재하지 않으면 이후 단계에서 생성).
   - 심볼릭 링크는 반드시 target 을 따라가지 말고 링크 자체만 `os.unlink`.
   - 권한 오류로 일부가 삭제되지 않으면 spawn 을 실패시킨다 (partial state 로 시작하지 않음).
3. **`AGENTS.md` 쓰기**: `agent_root/AGENTS.md`, `chmod 600`.
4. **`files` 맵 순회**: 각 항목에 대해 경로 validation (아래 "경로 검증" 규칙) → 상위 디렉토리 `mkdir -p 700` → 파일 쓰기 `chmod 600`.
5. **엔진 관례 심볼릭 링크 재생성**:
   - `CLAUDE.md` → `AGENTS.md`
   - `.agents/skills` → `../skills` (디렉토리 `.agents/` 먼저 생성)
   - `.claude/skills` → `../skills` (디렉토리 `.claude/` 는 `files` 맵의 `.claude/settings.json` 덕에 이미 존재)
6. **`engine_secrets` 렌더링**: 엔진 매핑에 따라 `.env` 로 기록 (e.g. `.gemini/.env`, `.codex/.env`), `chmod 600`.
7. **`workspace/` 보장**: 없으면 `mkdir 700`, 있으면 **그대로 둔다** (이전 spawn 에서 에이전트가 남긴 스크래치 파일 보존).
8. subprocess 를 `cwd=agent_root/workspace` 로 기동.

**경로 검증 규칙** (서버 insert + 머신 materialize 양쪽 공통):

- 화이트리스트 prefix: `skills/`, `.codex/`, `.claude/`, `.gemini/`, `.openhands/`
- 화이트리스트 확장자: `.md`, `.json`, `.toml`, `.txt`, `.yaml`, `.yml`
- 금지: 절대경로, `..` 세그먼트, 선행 `/`, 심볼릭 링크 표현, `workspace/*` (manifest 가 workspace 에 쓰지 못하도록)
- Null 바이트, 컨트롤 문자 금지
- 최대 path 깊이 6 레벨 권장 (deep 디렉토리 공격 방지)

**워크스페이스의 역할** 은 명시적으로 분리된다:

- **managed 트리** (AGENTS.md, skills/, .codex/, .claude/, .gemini/, .openhands/, 심볼릭 링크) — 100% 서버 manifest 에서 파생. 매 spawn 마다 prune + re-materialize.
- **workspace/** — 에이전트가 런타임에 파일을 만드는 스크래치 공간. Anygarden manifest 가 건드리지 않음. spawn 간 persistence 가 필요한 상태는 여기에 저장한다. 관리 주체는 에이전트 본인 (혹은 엔진의 파일 도구).

이 분리 덕분에 "manifest 는 서버가 선언적으로 관리, workspace 는 에이전트가 임시로 사용" 이라는 단순한 정신 모델이 성립한다.

## Phase 별 작업 브레이크다운

### Phase 0 — 공통 인프라 (완료, 2026-04-12)

- [x] 경로 validation 유틸 (서버 `anygarden.agent_files` + 머신 `anygarden_machine.agent_dir` 양쪽에 동일 규칙, 34 tests × 2) — `1b165e5`
- [x] `SpawnAgentFrame` 에 `agents_md`, `files`, `engine_secrets` 필드 추가 — `6791751`
- [x] `agent_files` 테이블 + alembic migration `005_agent_files_and_agents_md.py` — `5cbed62`
- [x] `agents.agents_md` 컬럼 추가 — `5cbed62`
- [x] `Spawner._materialize_agent_dir(msg)` — 디렉토리 생성 + **prune + 재작성** + 심볼릭 링크 + 경로 검증 + `engine_secrets` → `.env` 렌더링. 13 materialize tests — `8a93014`
- [x] `Spawner.spawn` 이 `create_subprocess_exec` 호출 시 `cwd=<agent_dir>/workspace` 전달 — `8a93014`
- [x] 서버 `AgentLifecycle.request_start` 가 DB 에서 `agents_md` + `agent_files` 읽어 프레임에 실어 보냄 — `1ed9b84`
- [x] 기존 `profile_yaml` 경로는 backward compat 으로 유지 (agents_md=None + files={} 시 legacy path 동작, 2 lifecycle tests) — `1ed9b84`
- [x] Cross-package E2E smoke test (server → frame → machine materialize, prune + workspace 보존 검증) — `8e45f3d`

**Phase 0 결과**: 서버 198 tests + 머신 75 tests + 신규 18 tests 모두 통과. 머신 데몬이 재시작 없이 새 spawn 프레임만 받으면 `~/.anygarden/agents/<id>/` 트리가 manifest 와 완벽히 일치하도록 reconcile 된다. `workspace/` 는 prune 대상에서 제외되어 에이전트의 런타임 상태가 spawn 간 보존된다.

**Phase 0 에서 발견해 설계에 역반영한 것**:
- Codex 리뷰가 원래 설계의 "files 맵을 추가만 함" 빈틈을 지적 → plan + ADR-002 에 **declarative reconcile** (prune + re-materialize) 규칙 명시. 이 원칙이 없었다면 "DB 에서 스킬 삭제해도 디스크에 남는" 유령 상태 버그가 production 에 들어갔을 것.
- `PurePosixPath(".env").suffix == ""` 파이썬 quirk 때문에 경로 검증에 dotfile 이름 특수 케이스 추가.

### Phase 1 — Codex + Gemini CLI 활성화 (완료, 2026-04-12)

- [x] **Codex 어댑터 sandbox 기본값 workspace-write** — `danger-full-access` 에서 내려옴. Phase 0 가 cwd=workspace 를 제공하므로 workspace-write 가 tightest sandbox. `fe291c5`
- [x] **Codex 어댑터 cwd 상속 전환** — 기존 `-C <temp_workdir>` + `mkdtemp` 조합 제거. Codex 가 자연스럽게 parent 디렉토리에서 AGENTS.md 탐색 (anygarden-agent 프로세스 cwd 가 workspace 로 이미 설정됨).
- [x] **Gemini CLI 신규 어댑터** — `anygarden-sdk/anygarden_sdk/integrations/gemini_cli.py`. `gemini -p <prompt> --output-format json` 패턴, 룸별 대화 컨텍스트, 느슨한 JSON 파싱 (response/text/content/output 순서로 탐색, bad json 시 raw fallback). 13 tests.
- [x] **Engine registry wiring** — `ENGINES["gemini-cli"]`, `_ADAPTER_CLASSES["gemini-cli"]`, `anygarden-agent` CLI `--engine gemini-cli` 분기 추가.
- [x] **Detector 확장** — `anygarden-machine` `BINARY_ENGINES` 에 `("gemini-cli", "gemini")` 추가하여 머신 register 시 capability 보고.
- [x] **start() breadcrumb** — 두 어댑터 모두 `Path.cwd().parent / "AGENTS.md"` 존재 여부를 로그로 남김. "왜 스킬이 안 로드되지?" 디버깅 첫 신호.

**Phase 1 결과**: SDK 54 tests, 머신 105 tests, 서버 198 tests 전부 통과. 구조적으로 Codex 와 Gemini CLI 는 쌍둥이 어댑터가 되었고, 둘 다 materializer 의 `~/.anygarden/agents/<id>/workspace/` 로 spawn 되어 `AGENTS.md` 자동 탐색한다.

**Phase 1 실측 + 4 건의 Codex sandbox 하드닝 (2026-04-12)**:

Playwright 로 실제 Codex 에이전트에 메시지를 보내 보면서 단위 테스트로는 못 잡는 버그 4개를 연속으로 발견·수정:

1. **`-o /tmp/...` 가 샌드박스 밖** (`3954c25`) — `tempfile.NamedTemporaryFile` 기본값이 `/tmp/` 인데 `-s workspace-write` 는 cwd 외부 쓰기를 차단. 응답 파일 생성 자체가 실패해서 에이전트가 조용히 응답 없음. 수정: `tempfile.NamedTemporaryFile(dir=str(Path.cwd()))` 로 cwd 내부에 생성.

2. **`-C` 플래그 누락** (`676b6e3`) — 최초 구현은 `-C` 를 생략하고 codex 가 cwd 에서 알아서 AGENTS.md 를 찾기를 기대했으나, codex 의 AGENTS.md 탐색은 `-C <root>` 에서부터 **다운워드** 로만 진행됨. `--skip-git-repo-check` 는 상향 탐색을 막지 못해서 codex 가 조상 git repo (`/home/e7217/projects/anygarden`) 를 프로젝트 루트로 오인하고 우리 AGENTS.md 를 전혀 못 봤음. 첫 실측에서 응답이 AGENTS.md 규칙을 완전히 무시한 원인. 수정: `-C str(Path.cwd().parent)` (= agent_root) 를 명시적으로 전달.

3. **`-C agent_root` 는 샌드박스 너무 넓힘** (`dc74cb7`) — `-C agent_root` 로 잡으면 `workspace-write` 샌드박스의 `workdir` 이 agent_root 전체로 확장됨. 즉 실행 중인 에이전트가 자기 자신의 `AGENTS.md`, `skills/*/SKILL.md`, `.codex/config.toml` 등을 **세션 중에 덮어쓸 수 있음**. Prune 은 "다음 spawn" 에만 복원하므로 현재 세션의 지시문이 변조될 수 있음 (prompt injection 공격 가능). 수정: `-C str(Path.cwd())` (= workspace/) 로 되돌리고, materializer 에 좁은 예외로 `workspace/AGENTS.md → ../AGENTS.md` 심볼릭 생성. Codex 는 cwd 에서 AGENTS.md 발견, 진짜 파일은 샌드박스 밖. 쓰기 시도는 심볼릭을 unlink + 로컬 파일 생성 패턴으로만 가능한데 그건 세션 스코프일 뿐 다음 spawn 에서 심볼릭 복원.

4. **`workspace/AGENTS.md` 심볼릭이 `agents_md=None` 경로에서 dangling** (`91a6785`) — 이전 spawn 에서 agents_md 가 설정돼 있어서 심볼릭이 생성됐는데, 다음 spawn 에서 agents_md 가 비면 `agent_root/AGENTS.md` 가 prune 으로 삭제됨. workspace/ 는 prune 대상 제외라 심볼릭은 그대로 남고, 가리킬 대상 없는 dangling 상태. 수정: materializer 가 `workspace/AGENTS.md` 를 양방향 reconcile (agents_md 설정 시 생성, 비면 삭제).

**Phase 1 실측 검증**: 스킬 2개 (`greeting`, `time-check`) 모두 AGENTS.md 규칙대로 `[SKILL: <name>]` 접두사 + 지정 포맷으로 응답. `date` 명령 실행 결과까지 깔끔히 포함.

**별도 발견: Codex 는 파일 기반 skill discovery 를 안 한다** — 처음엔 스킬이 작동하는 것처럼 보였지만 자세히 보면 순전히 AGENTS.md 에 `## 가용 스킬` 섹션으로 규칙을 수동 인라인 했기 때문. Codex 는 `~/.codex/skills/` 글로벌 경로만 로드하고 프로젝트 로컬 `skills/<name>/SKILL.md` 는 인식 안 함. → **Phase 1.5 로 materializer 차원에서 해결**.

### Phase 1.5 — SKILL.md auto-inline into AGENTS.md (완료, 2026-04-12)

- [x] **`Spawner._compose_agents_md(msg)` 헬퍼** (`c4beeeb`) — base `msg.agents_md` 뒤에 `## Available skills` 섹션을 자동 append. 모든 `skills/*/SKILL.md` 본문을 path 정렬 순서로 concatenate. 섹션 헤더에 "(auto-generated)" 표기로 수동 콘텐츠와 구분.
- [x] **`_materialize_agent_dir` 에 적용** — `msg.agents_md` 직접 쓰지 않고 `self._compose_agents_md(msg)` 결과를 기록.
- [x] **3 새 materialize tests** — (1) 스킬 없으면 base 그대로, (2) 스킬 있으면 base + auto 섹션 + 정렬 순서 검증, (3) 비스킬 파일만 있으면 섹션 안 생김.
- [x] **Playwright 실측** — AGENTS.md 에서 `## 가용 스킬` 섹션을 **일부러 제거** 한 최소 버전으로 seed. Codex 가 auto-inlined 섹션을 읽어 `[SKILL: greeting]` / `[SKILL: time-check]` 규칙을 그대로 준수한 응답 반환. Admin 이 수동으로 스킬을 나열할 필요 없어짐.
- [x] **Claude Code 와의 양립성** — Claude Code 는 `.claude/skills/<name>/SKILL.md` 네이티브 discovery 도 하고 CLAUDE.md 에 인라인된 스킬 본문도 봄. 경미한 중복이 있지만 무해.

**Phase 1.5 철학**: 파일 기반 skill discovery 를 지원하지 않는 엔진은 materializer 가 "fat AGENTS.md" 를 렌더링해서 보여주면 됨. 어댑터 코드는 그대로 얇게 유지.

### Phase 1.6 — Gemini CLI 실측 하드닝 (완료, 2026-04-12)

gemini 0.37.1 바이너리를 dev box 에 설치한 뒤 Playwright 실측에서 두 가지 버그 발견 → 수정:

1. **`agent_root` 를 서브프로세스 cwd 로 고정** — 최초 구현은 codex 와 동일하게 cwd=workspace/ 를 상속받고 `.gemini/settings.json` 을 agent_root/.gemini/ 에 뒀음. 근데 gemini 의 `findProjectRoot` 가 `.git` 을 **위로 탐색** 해서 프로젝트 루트를 결정하는데, per-agent 레이아웃엔 `.git` 이 없으니 cwd (= workspace/) 를 "project root" 로 고정하고 `.gemini/settings.json` 을 agent_root 에서 찾아주지 않음. 결과적으로 gemini 가 `context.fileName = "AGENTS.md"` 설정을 **절대 로딩 못 함** → AGENTS.md 가 계약된 hierarchical memory 로 포함 안 됨 → 스킬 규칙 완전 무시. 실측에서 첫 응답은 그저 "반갑습니다! Gemini CLI 엔진입니다..." 라는 stock gemini 세션. 수정: 어댑터가 `asyncio.create_subprocess_exec(cwd=str(Path.cwd().parent))` 로 agent_root 에서 기동. gemini 가 agent_root 를 project root 로 잡으면서 `.gemini/settings.json` + `AGENTS.md` 를 hierarchical memory 로 자동 로딩.

2. **`--approval-mode yolo`** — 비대화형 `-p` 모드에서도 gemini 기본 approval 모드는 `default` (= "prompt for approval") 라서 tool 호출 (shell exec) 시 사람의 승인을 기다림. 실측에서 time-check 스킬이 120s 타임아웃까지 응답 없음 → 결국 어댑터가 fail. 수정: 어댑터가 `--approval-mode yolo` 를 argv 에 명시. codex / claude-code 와 동일한 autonomous trust model. YOLO 배너는 stderr 로 나가므로 stdout JSON 파서에 영향 없음.

3. **`workspace/AGENTS.md` / `workspace/CLAUDE.md` 를 engine-aware hybrid 로 전환** — gemini 의 `read_file` 툴이 파일 경로를 resolve 한 뒤 "allowed workspace directories" 를 벗어나면 거부 (`Path not in workspace: Attempted path resolves outside allowed workspace directories`). `../AGENTS.md` 심볼릭은 resolve 후 `agent_root/AGENTS.md` 가 돼서 바로 거부됨. codex 심볼릭 패턴은 그대로 못 씀.

    **첫 시도 (real file copy for all engines)** 는 Codex stop-hook 에서 regression 으로 지적: symlink 설계는 "read 는 통과 / write 는 sandbox 경계에서 거부" 라는 **in-session prompt injection 방어** 를 isolation 계약으로 가지고 있었는데, real file copy 는 workspace 내부 mutable 파일이라 에이전트가 자기 자신의 AGENTS.md 를 세션 중에 overwrite 가능. 각 턴이 fresh 서브프로세스로 AGENTS.md 를 re-read 하는 codex/gemini 모델에서 이건 실제 공격 벡터.

    **최종 수정**: `Spawner._materialize_agent_dir` 가 `msg.engine` 에 따라 분기한다.
    - `engine == "gemini-cli"` → real file copy, **mode 0o400** (owner read-only). `open(..., O_WRONLY)` 가 EACCES 로 실패하는 speedbump. `chmod u+w` 우회 가능하나 (a) 노이지 (쉘 로그), (b) 다음 spawn 의 materializer 가 canonical bytes 로 복원 → tamper 는 one session scope.
    - `engine in {codex, claude-code}` → 원래 symlink (`workspace/AGENTS.md -> ../AGENTS.md` + `workspace/CLAUDE.md -> ../CLAUDE.md`). Codex 리뷰가 확정한 isolation 계약 그대로 유지.

    신규 테스트 (materialize): `test_creates_workspace_agents_md_symlink_for_codex`, `test_creates_workspace_claude_md_symlink_for_codex`, `test_creates_workspace_agents_md_real_copy_for_gemini`, `test_creates_workspace_claude_md_real_copy_for_gemini`. Tamper 회복: `test_workspace_agents_md_refreshed_even_if_tampered_gemini` (chmod + overwrite → 다음 spawn 이 0o400 canonical 복원), `test_workspace_agents_md_symlink_restored_after_tamper_codex` (symlink unlink + regular file 바꿔치기 → 다음 spawn 이 symlink 복원).

4. **pre-existing `test_e2e_materialize.py` 수정** — Phase 1.5 가 `AGENTS.md` 에 `## Available skills` 섹션을 자동 인라인하도록 바꿨는데 이 e2e 테스트가 `assert rendered == "# e2e agent\nBe helpful."` 그대로 남아 있어서 실패 상태. `startswith` + 인라인 섹션 확인으로 교체.

**Phase 1.6 실측 검증**:
- `@테스트 에이전트 Gemini CLI agent_root cwd 적용 후 — 안녕!` → `[SKILL: greeting] 안녕하세요! 저는 Anygarden 테스트 에이전트입니다. 코딩 어시스턴트입니다. 도와드릴 일이 있을까요?` ✅
- `@테스트 에이전트 yolo 모드 적용 후 — 지금 몇 시야?` → `[SKILL: time-check] $ date '+%Y-%m-%d %H:%M:%S %Z' 현재 시각은 2026년 4월 12일 오전 2시 39분 17초(KST)입니다.` ✅

**신규 회귀 테스트**: `TestCallGemini::test_cwd_is_agent_root_and_approval_mode_yolo` — `asyncio.create_subprocess_exec` 를 monkeypatch 해서 cwd (agent_root) 와 `--approval-mode yolo` 가 argv 에 들어가는지 고정. SDK 15 gemini tests (+1).

**세 개 엔진 일관성 증명**: 이제 dev box 의 실측 가능한 3개 엔진 (codex / claude-code / gemini-cli) 이 동일한 AGENTS.md + skills/ 파일만 가지고 `[SKILL: greeting]` / `[SKILL: time-check]` 규칙을 **완전히 동일한 포맷으로** 준수. 한 개의 materializer manifest → 3개 엔진에서 identical 행동이라는 Phase 0 의 약속이 이제 세 엔진 모두에서 증명됨.

### Phase 2 — Deep Agents 활성화 (~2h)

- [ ] `deep_agents.py` 어댑터 재작성 (현재는 스텁에 가까움)
- [ ] `create_deep_agent(backend=FilesystemBackend(root_dir=agent_dir, virtual_mode=True), skills=["/skills/"], memory=["/AGENTS.md"], checkpointer=..., model=profile.model)`
- [ ] `HumanInTheLoopMiddleware` 또는 `interrupt_on={"write_file": True, "execute": True}` 로 write 툴 보호
- [ ] `thread_id` 는 room_id 기반 (룸별 대화 컨텍스트 분리)
- [ ] 테스트: AGENTS.md + 2개 스킬이 있는 디렉토리를 생성하고 Deep Agent 가 스킬을 호출해 응답하는 E2E

### Phase 3 — Claude Code SDK 활성화 (완료, 2026-04-12)

- [x] **`claude_code.py` 어댑터 재작성** (`e707476`) — conceptual 스텁 제거, `claude-agent-sdk` (전 `claude-code-sdk`) 드라이버로 교체. `pyproject.toml` 의 `[project.optional-dependencies]` 에서 `claude-code = ["claude-agent-sdk>=0.1.0"]` 로 이름 이전.
- [x] **`ClaudeAgentOptions(cwd=str(Path.cwd()), setting_sources=["project"])`** — **두 필드 모두 비가역**. `setting_sources=None` (기본값) 이면 CLAUDE.md 와 project skills 가 조용히 무시됨. 단위 테스트에서 정확한 값 pin.
- [x] **룸별 `resume` 세션 유지** — `_last_session_id` 를 쿼리 drain 중 포착하고 `integrate_with_claude_code` wrapper 가 `_sessions[room_id]` 에 반영. 다음 호출이 같은 session 이어받음.
- [x] **`_collect_reply` 의 TextBlock 필터링** — `AssistantMessage.content` 의 `TextBlock` 만 추출, `ToolUseBlock` / `ToolResultBlock` 의 `text` 는 skip. Skill 활성화 시 `ToolResultBlock` 에 SKILL.md 원문이 담기는데, 처음엔 그걸 응답으로 leak 하는 버그가 있었음. `ResultMessage.result` 가 있으면 그 값을 우선 사용.
- [x] **Detector 수정** — Claude Code 바이너리는 `claude` (not `claude-code`). `BINARY_ENGINES = [("claude-code", "claude"), ...]`. 이전 구현은 `shutil.which("claude-code")` 로 찾아 항상 miss 했음.
- [x] **Materializer `workspace/CLAUDE.md → ../CLAUDE.md` 심볼릭** — AGENTS.md bridge 와 동일 패턴. Claude Code SDK 가 cwd 에서 CLAUDE.md 를 직접 발견. 양방향 reconcile (생성/삭제 둘 다).
- [x] **CLI 분기 업데이트** — `_setup_engine()` 의 `elif engine == "claude-code"` 에 `system_prompt=None` (CLAUDE.md 가 주인) + `model=` 전달.
- [x] **8 Claude Code tests** — fake SDK 모듈로 `query()` 녹음, cwd/setting_sources/system_prompt/model/resume/룸 격리/ToolBlock leak 방지 핀.
- [x] **Playwright 실측** — `@테스트 에이전트 Claude Code 엔진 재시도 - 안녕하세요` → `[SKILL: greeting] 안녕하세요! 저는 Anygarden 테스트 에이전트입니다. 코딩 어시스턴트로서...`. `@테스트 에이전트 지금 몇 시야?` → `[SKILL: time-check] $ date '+%Y-%m-%d %H:%M:%S %Z' ... 2026년 4월 12일 오전 1시 50분 49초 (KST)`. 두 스킬 모두 **`.claude/skills/*/SKILL.md` 네이티브 SDK discovery 로 자동 로드** — Codex 와 달리 수동 인라인 없이 동작.

### Phase 4 — OpenHands 마이그레이션 (분리, 후순위)

- [ ] V0 TOML 설정 기반 어댑터 제거
- [ ] `openhands.sdk` (Software Agent SDK) 로 재작성
- [ ] `Conversation(agent=Agent(llm=...), workspace=<path>)` 사용
- [ ] `.openhands/microagents/` 생성 — SKILL.md 를 microagent 포맷으로 변환하는 generator
- [ ] AGENTS.md 는 adapter 가 파싱해 LLM system prompt 로 직접 주입

### Phase X — 보너스 (큰 결정, 별도 설계 세션 필요)

Deep Agents 의 `AsyncSubAgent` 는 LangGraph SDK 클라이언트를 통해 **Agent Protocol 호환 원격 서버** 를 sub-agent 로 호출할 수 있다. 만약 anygarden-machine 이 Agent Protocol 엔드포인트 (`/threads`, `/runs`, ...) 를 노출하면, 한 Deep Agent 인스턴스가 Anygarden 의 다른 에이전트들을 서로 호출하는 멀티에이전트 오케스트레이션 레이어가 사실상 공짜로 생긴다. 별도 ADR + 프로토콜 매핑 설계 필요. Phase 0–4 완료 후 재검토.

**중요 분리**: Agent Protocol 서버는 Deep Agents 와 무관하게 유용하다. HTTP 를 말할 수 있는 누구든 (curl / cron / n8n / LangGraph SDK / AutoGen / CrewAI / Deep Agents) 호출자가 될 수 있다. Deep Agents 는 "공짜로 쓸 수 있는 준비된 클라이언트" 일 뿐, 구현 전제가 아니다.

#### Phase X 레퍼런스 패턴 — pi-mono + OpenHands V1 (2026-04-12 조사)

Phase X 를 실제로 착수할 때 "어떤 sub-agent + streaming 모델을 따를지" 결정에 쓰이도록 두 프레임워크의 최신 설계를 문서화한다. 두 프레임워크는 **정반대 철학**:

**pi-mono (badlogic/mariozechner, TypeScript)**:
- **Sub-agent 공식 미지원**. "ships with powerful defaults but skips features like sub agents and plan mode" — 의도적.
- 대신 `pi --print --provider X --model Y "prompt"` 로 자기 자신을 bash subprocess 로 재귀 호출.
- Background bash 는 의도적으로 미지원 — **tmux 세션 spawn 만**. 이유: full observability + 사람이 직접 들여다보고 개입 가능.
- Streaming: `pi-ai` 패키지의 `AssistantMessageEventStream` 이벤트 프로토콜. **Progressive tool args parsing** 이 차별점 — LLM 이 tool arguments 를 토큰 단위로 흘려보내는 동안 pi-ai 가 점진적으로 JSON 파싱 → diff 가 쓰이는 동안 UI 에서 그려지는 효과.
- 4개 wire protocol 만 추상화 (OpenAI Completions, OpenAI Responses, Anthropic Messages, Google Generative AI).
- Google Generative AI 는 streaming tool args 미지원 → pi-ai 가 `toolcall_start → toolcall_delta → toolcall_end` 버스트로 normalize.
- Abort control + steering message queue 내장.
- Fork `oh-my-pi` 가 이 누락을 채우는 방향으로 가서 parallel execution + 6 bundled agents (explore, plan, designer, reviewer, task, quick_task) + real-time artifact streaming 을 추가 — 즉 "공식이 제공 안 하면 fork 가 제공".

**OpenHands V1 SDK (All-Hands-AI, Python, MLSys 2026 oral)**:
- **`DelegateTool` standard tool** 로 sub-agent delegation 공식 제공. `openhands.tools` 패키지. V1 SDK 에서만.
- 두 단계 command:
  - `spawn`: sub-agent ids 리스트 정의 (`["research", "implementation", "testing"]`).
  - `delegate`: 각 id 에 task description 매핑 (`{"research": "...", ...}`).
- **Parallel threads 실행** + **blocking**: parent 가 모든 sub-agent 완료를 동기 대기 → 모든 결과가 하나의 **consolidated observation** 으로 parent LLM 에 반환.
- Sub-agent 는 parent LLM config + workspace 상속, conversation context 는 독립.
- Streaming: `Conversation(..., token_callbacks=[fn])` 로 토큰 콜백 등록. `ModelResponseStream` chunk 에서 4가지 스트림 타입 분리:
  1. `reasoning_content` (o1/thinking 모델의 CoT)
  2. `content` (일반 assistant 텍스트)
  3. `tool_calls[].function.name` (tool 이름)
  4. `tool_calls[].function.arguments` (tool arguments)
- 제약: "chat completions endpoint 에서만" streaming 지원.
- Sub-agent delegation 과 streaming 의 직접 상호작용은 **공식 문서에 없음** — delegation 이 blocking 이므로 parent 의 `token_callbacks` 에는 child 의 토큰이 안 흐르고 consolidated observation 만 보임.
- V0 은 2026-04-01 제거. V1 은 event-sourced state, immutable config, typed tool system + MCP.

**두 프레임워크 차이 요약**:

| 측면 | pi-mono | OpenHands V1 |
|---|---|---|
| Sub-agent 형태 | 없음 (bash/tmux 재귀) | 표준 tool (`DelegateTool`) |
| 병렬성 | tmux 로 프로세스 단위 병렬 | threads 병렬 |
| Blocking | 호출자 bash 가 대기 | parent LLM 이 대기 |
| 결과 합치기 | 호출자가 stdout 파싱 | consolidated observation 자동 |
| 관찰성 | tmux pane 을 사람이 직접 | `token_callbacks` 이벤트 |
| Sub-agent 스트리밍 | N/A (프로세스 경계) | 공식 미지원 (blocking tool 특성) |
| Tool args progressive parse | ✅ (pi-ai 차별점) | 토큰 단위 분리는 있으나 diff 렌더 수준은 아님 |
| 철학 | "조합은 사용자 몫" | "표준 tool 로 병렬 위임 + 통합 공식 지원" |

#### anygarden Phase X 시사점

1. **anygarden 의 채널 기반 서브룸 (`Room.parent_room_id`)** 은 pi-mono 의 bash/tmux spawn 패턴과 심리적으로 가깝다 — "sub-agent = 새 entity" 가 아니라 "같은 workspace 의 또 다른 invocation". 지금도 잘 동작.

2. **Phase X Agent Protocol 방향** 은 OpenHands `DelegateTool` + Deep Agents `AsyncSubAgent` 와 일치하는 "blocking parallel RPC + consolidated result" 패턴. anygarden-machine 이 `POST /threads (= spawn)` + `POST /threads/{id}/runs (= delegate)` 로 매핑하면 모델이 거의 같다.

3. **Streaming 격차**: 현재 anygarden 의 엔진 어댑터들 (codex / claude-code / gemini-cli) 은 subprocess 응답이 **전체 완성된 후** WebSocket 으로 `send()` 하는 구조라 "에이전트가 타이핑 중" UX 가 없다. pi-ai 의 progressive tool args parsing, OpenHands 의 `token_callbacks` 4-stream 분리가 Phase 4 OpenHands V1 마이그레이션 + 다른 어댑터 업그레이드의 reference.

4. **Sub-agent 스트리밍 — anygarden 고유 이점**: pi-mono, OpenHands V1 둘 다 "sub-agent 의 부분 출력을 parent 에게 직접 흘려보내는" 건 공식 미지원. anygarden 의 WebSocket 채널 모델은 "A와 B가 같은 방에 있으면 B의 응답이 A에게 **자연스럽게 스트리밍**" 되므로 **이 부분은 오히려 우위**. Phase X 에서 Agent Protocol 모델을 추가해도 채널 기반 모델을 버리지 않고 **공존** 시키는 게 올바른 방향.

5. **Phase 2 Deep Agents 실측 근거 수집 우선**: Phase X 설계는 Deep Agents 의 `AsyncSubAgent` 가 실제로 어떻게 원격을 호출하는지 실측해야 구체화 가능. Phase 2 (Deep Agents 어댑터 재작성) 가 먼저 완료돼야 Phase X 의 프로토콜 매핑 결정 (어느 Agent Protocol 스펙을 따를지: AI Engineer Foundation vs LangGraph Platform vs OpenAI Assistants) 에 실증 근거가 생긴다.

#### Phase X 전에 결정해야 할 것들

1. **스펙 선택**: AI Engineer Foundation Agent Protocol (`/ap/v1/agent/tasks`) vs LangGraph Platform Runtime API (`/threads`, `/runs`, `/assistants`) vs OpenAI Assistants API. Deep Agents 통합이 1순위라면 LangGraph flavor 선택 강제.
2. **Thread ↔ Room 매핑**: Agent Protocol `thread_id` 가 anygarden 의 새 room 에 대응? 아니면 기존 room 의 sub-room? 아니면 완전히 분리된 네임스페이스?
3. **인증**: 현재 `agent_token` 은 WebSocket subprotocol 헤더. Agent Protocol 은 HTTP Authorization 헤더 — 같은 토큰 재사용 vs 별도 스킴.
4. **Server vs Machine 위치**: Agent Protocol 엔드포인트를 anygarden-server 가 노출 (중앙 진입점 깔끔, machine 으로 프록싱 필요) vs anygarden-machine 이 직접 노출 (multi-machine 환경에서 라우팅 문제).
5. **Sub-agent 스트리밍 정책**: OpenHands 처럼 blocking + consolidated 만 지원할지, anygarden 의 채널 모델을 살려 **스트리밍 delegation** 까지 지원할지 — 후자면 Agent Protocol 스펙 자체를 확장해야 할 수 있음.

## 보안 고려사항

- **경로 검증**: `_materialize_agent_dir` 는 파일 쓰기 전에 경로 화이트리스트 검사. 서버 쪽에서도 `agent_files` insert 시 동일 검증.
- **권한**: 생성된 파일은 모두 `chmod 600` (에이전트 실행 유저 전용 읽기/쓰기). 디렉토리는 `chmod 700`.
- **Deep Agents FilesystemBackend**: 상위 docstring 이 "웹서버 프로세스 안에서 쓰지 말라" 고 명시. Anygarden 의 subprocess-per-agent 구조가 이 우려를 해결 — Deep Agent 는 머신 데몬의 자식 프로세스에서만 돌고 FastAPI 서버 프로세스에는 들어가지 않음. `virtual_mode=True` 를 반드시 사용해 path escape 방지.
- **MCP 서버 주입 경로**: `.codex/config.toml`, `.gemini/settings.json`, `.claude/settings.json` 은 모두 "임의 명령 실행 가능" 한 설정 파일. 서버 admin 이 악의적인 MCP 서버 선언을 넣을 수 있음. 이건 admin 권한 신뢰 범위 안이지만, 머신 오너가 서버 admin 과 다를 수 있으므로 머신 쪽에서도 MCP 서버 선언을 drop/warn 할 수 있는 allowlist 를 추후 도입.
- **`.env` 시크릿**: `engine_secrets` 는 DB 에 평문 저장하지 말 것 — 별도 envelope 암호화 필요 (별도 ADR 예정).

## Open questions

1. **`agent_files` 편집 UI** — admin 이 웹 UI 에서 agent 파일 트리를 편집할 수 있어야 하는가, 아니면 REST API 만으로 충분한가? ~~(Phase 0 에서는 API 만, UI 는 별도 phase.)~~ **해결됨 (2026-04-12)**: PR #8 이 REST 엔드포인트 (`PUT /agents/{id}`, `/agents/{id}/files` CRUD) 를, PR #9 가 `AgentEditDialog` (AGENTS.md textarea + 파일 트리 + 그룹 뷰) 를 추가. 비-개발자 admin 을 위한 UX 개선 (스킬 라이브러리, MCP 마켓 등) 은 [`2026-04-12-agent-editor-future-ux.md`](2026-04-12-agent-editor-future-ux.md) 로 분리됨.
2. **파일 수정 시 에이전트 재기동 여부** — `agent_files` 를 수정했을 때 에이전트를 자동 restart 해야 하는가, 또는 다음 spawn 에만 반영되는가? 엔진마다 파일 재로드 시점이 다름 (Codex 는 매 exec, Claude Code 는 매 query, Deep Agents 는 프로세스 기동 시 1회). 단순화를 위해 Phase 0 는 "spawn 시점 반영만" 으로 시작.
3. **프레임 크기 한계** — WebSocket 프레임 인라인 전달은 수백 KB 까지 현실적. 수 MB 넘으면 별도 HTTP GET 엔드포인트 + 머신 pull 로 전환 필요. 경고 임계값은 512KB.
4. **`engine_secrets` 의 출처** — 서버 DB 에 저장 vs 머신 로컬 `~/.anygarden/engine-secrets.toml` 에 저장. 후자가 안전하지만 중앙 관리 불가. 절충안: 서버는 "이 에이전트가 어떤 키를 쓰는지만" 저장하고 머신 로컬에서 키를 lookup.

## 관련 문서

- `docs/decisions/002-per-agent-directory-with-server-manifest.md` — 아키텍처 결정 기록
- `docs/decisions/001-engine-subprocess.md` — 기존 subprocess 전환 결정 (이 설계의 전제 조건)
