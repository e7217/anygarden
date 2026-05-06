---
id: 2
title: Per-agent directory on machine, sourced from server manifest
status: amended
date: 2026-04-11
---

# 2. Per-agent directory on machine, sourced from server manifest

> Amendment 2026-05-06 (#345): `doorae-agent` subprocesses now start
> with `cwd=<agent_dir>` rather than `cwd=<agent_dir>/workspace/`.
> Materialize refreshes only managed top-level entries and preserves
> agent-created root output. `workspace/` remains only as a codex SDK
> sandbox fallback while codex lacks read-only path exceptions.
>
> Amendment 2026-05-07 (#350): `skills/` is now a runtime-owned
> directory after initial seeding. Normal materialize preserves agent
> edits and agent-authored skills, while control-plane files remain
> materializer-managed. Codex fallback workspaces bridge
> `workspace/skills -> ../skills`.

## Context

각 에이전트의 개성 (역할, 참조 문서, 스킬 세트, 엔진 고유 설정) 을 단일 `system_prompt` 에만 담아 전달하는 방식은 한계에 닿았다. 구체적으로:

- Codex CLI 는 cwd 상향 탐색으로 `AGENTS.md` 를 자동 로드하는 네이티브 메커니즘을 가지고 있으나, Doorae 는 subprocess 를 의미 없는 cwd 로 기동해 이 기능을 못 쓰고 있다.
- Claude Code SDK 는 `.claude/skills/<name>/SKILL.md` 를 project-local 로 discovery 하지만, Doorae 에는 그런 위치를 구성할 자리가 없다.
- Gemini CLI 는 `.agents/skills/` 와 `.gemini/settings.json` 을 표준 레이아웃으로 인식하지만 역시 Doorae 에서 활용되지 못한다.
- Deep Agents (langchain-ai/deepagents) 는 `FilesystemBackend(root_dir=...)` + `skills=[...]` + `memory=["AGENTS.md"]` 로 에이전트 디렉토리를 직접 소비하도록 설계되어 있다.
- SDK 프로필 스키마의 `mcp_servers` 필드는 어떤 어댑터에서도 읽히지 않는 dead field 상태.

반면에 admin 이 각 머신에 SSH 해서 파일을 드롭하는 방식은 규모가 커지면 유지 불가능하고, 중앙 관리 (누가 무엇을 바꿨는지) 도 불가능하다.

에이전트가 실제로 파일을 읽어야 하는 장소는 머신 로컬 디스크지만, 파일의 내용 자체는 중앙에서 관리되어야 한다는 **이원 구조** 가 필요했다.

## Decision

에이전트마다 **머신 로컬 파일시스템에 고정된 디렉토리** `~/.doorae/agents/<agent_id>/` 를 배치한다. 이 디렉토리의 구조는 다음 원칙을 따른다:

1. **`AGENTS.md` + `skills/<name>/SKILL.md` 가 에이전트가 읽는 표준 투영점**. [agents.md](https://agents.md) 와 [agentskills.io](https://agentskills.io) 표준을 채택. `skills/`는 초기 manifest seed 뒤에는 agent-owned runtime 영역으로 보존한다.
2. 엔진별 관례 파일명/경로 (`CLAUDE.md`, `GEMINI.md`, `.agents/skills/`, `.claude/skills/`) 는 모두 심볼릭 링크 또는 자동 렌더링 결과물.
3. 엔진별 설정 파일 (`.codex/config.toml`, `.gemini/settings.json`, `.claude/settings.json`) 은 spawner 가 에이전트 프로필로부터 자동 생성.
4. subprocess 는 항상 `cwd=<agent_dir>/` 로 기동한다. Codex만 표준 `workspace-write` 샌드박스 보호를 위해 SDK thread cwd로 `<agent_dir>/workspace/` fallback을 쓸 수 있다.
5. in-process 엔진 (Deep Agents) 은 `FilesystemBackend(root_dir=<agent_dir>, virtual_mode=True)` 로 동일한 레이아웃을 소비.

**파일의 내용물은 서버 DB 가 source of truth**. 서버에는 다음 두 가지 저장소가 추가된다:

- `agents.agents_md` — AGENTS.md 본문 (`Text` 컬럼)
- `agent_files` 테이블 — `(agent_id, path, content)` 튜플로 에이전트 디렉토리 트리를 표현

Admin 은 REST API 로 파일을 CRUD 하고, 이는 DB 에만 반영된다. 실제 디스크는 에이전트를 spawn 하는 순간 머신이 만든다. 즉:

- **서버 PC** = manifest (에이전트가 어떤 파일들을 가져야 하는가 — 내용 전부 포함)
- **머신 PC** = materializer (spawn 시점에 manifest 를 받아 로컬 디스크에 풀어놓음)

`spawn_agent` WebSocket 프레임이 확장되어 `agents_md` 와 `files: dict[str, str]` 필드를 싣는다. 머신의 `Spawner._materialize_agent_dir(msg)` 는 **declarative reconcile** 을 수행한다:

1. 에이전트 루트 생성 (없으면)
2. **Prune**: control-plane managed 파일/디렉토리/심볼릭 링크(`AGENTS.md`, `CLAUDE.md`, `.mcp.json`, 엔진 config 등)를 삭제 후 재생성한다. `skills/`와 `memory/`는 agent runtime 영역이라 normal spawn에서는 보존한다
3. AGENTS.md 기록 (chmod 600)
4. `files` 맵 순회 → 경로 validation → control-plane 파일 쓰기 (chmod 600). `skills/**`는 target이 없을 때만 seed하고 기존 파일은 보존한다
5. 엔진 관례 심볼릭 링크 재생성 (CLAUDE.md → AGENTS.md, .agents/skills → ../skills 등)
6. Codex 엔진이면 `workspace/` fallback과 `workspace/skills -> ../skills`, `workspace/memory/*` bridge를 보장한다
7. subprocess 를 `cwd=<agent_dir>/` 로 기동

**control-plane vs runtime 영역**: materializer 는 `AGENTS.md`, `CLAUDE.md`, `.mcp.json`, 엔진 config를 서버 manifest의 안정적인 투영으로 복구한다. `skills/`와 `memory/`는 에이전트가 개선하거나 생성한 내용을 잃지 않도록 보존한다. 강제 재동기화/삭제는 별도 reset/sync 작업으로 다룬다.

## Alternatives considered

- **Machine-local authoring**: admin 이 각 머신에 SSH 해서 파일 관리. 기각 — 규모 불가능, 중앙 제어 불가.
- **Git repo pointer**: 에이전트 프로필에 git URL + ref 를 두고 머신이 `git clone/pull`. 기각(유보) — 스킬을 이미 git 으로 관리하는 팀에는 자연스럽지만, doorae 가 git 의존성을 머신 데몬에 추가하는 것은 과도. 이후 phase 로 재검토 가능.
- **Managed Agents API (Anthropic) 에 위임**: Claude 의 /v1/agents + /v1/skills 업로드를 통해 skill 관리를 Anthropic 에 맡기는 방법. 기각 — 특정 벤더 종속, self-host 불가, Bedrock/Vertex 미지원, Doorae 의 멀티엔진 지향과 모순.
- **API endpoint per-file pull**: 머신이 spawn 시 서버에 HTTP GET 으로 파일들을 pull. 유보 — 현재 프레임 인라인 전달로 수백 KB 까지 충분. 대용량 필요 시 이후 phase.

## Consequences

**긍정적:**

- Codex CLI, Gemini CLI, Claude Code SDK, Deep Agents 가 모두 **네이티브 메커니즘으로** 지시문/스킬/MCP 를 인식 → 어댑터 코드 감소
- 같은 파일 포맷(`AGENTS.md`, `SKILL.md`) 을 여러 엔진이 공유 → 팀이 한 번 작성한 스킬을 엔진 전환 없이 재사용
- 서버가 단일 source of truth 이므로 admin UI / audit log / 권한 관리가 중앙에서 일관됨
- dead field 였던 `mcp_servers` 가 실제로 동작 (엔진별 config 파일로 렌더링)
- Codex sandbox mode 같은 엔진별 설정이 에이전트마다 독립적으로 지정 가능

**부정적:**

- `spawn_agent` 프레임 크기가 증가 — 에이전트당 수백 KB 가 현실적, 수 MB 넘으면 별도 전송 경로 필요
- 서버/머신 양쪽에 경로 validation 을 중복 구현해야 함 (defense in depth)
- 보안 경계가 늘어남: 서버 admin 이 머신 로컬에 임의의 MCP 서버 선언을 푸시할 수 있음. 머신 오너와 서버 admin 이 다른 조직일 경우 추가 allowlist 필요
- 파일의 추가/수정/삭제가 에이전트 동작에 반영되려면 re-spawn 이 필요 (엔진마다 재로드 시점이 달라 hot-reload 일반화 어려움). Prune + reconcile 덕분에 **삭제도 결정적으로 반영**된다는 보장은 유지
- 기존 `profile_yaml` 기반 흐름과 병행 운영 → backward compat 코드 유지 비용

**기각된 대안 대비 이점:**

- Machine-local authoring 대비: 중앙 관리 + audit trail
- Git pointer 대비: 외부 의존성 없음, 오프라인 동작, 프로토콜 단순
- Managed Agents (Anthropic) 대비: 벤더 중립, 멀티엔진, 데이터 주권

## 관련 문서

- 구현 계획: `docs/plans/2026-04-11-per-agent-directory-skills.md`
- 기존 subprocess 전환 결정: `docs/decisions/001-engine-subprocess.md`
