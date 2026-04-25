# feat(machine): bridge memory/shared/ into agent workspace (#257)

- Commit: `81d622c` (81d622c30ca4926c626f744b1b6da8790d1bc195)
- Author: Changyong Um
- Date: 2026-04-25T15:12:40+09:00
- PR: #257 (이슈), 머지 PR 별도 생성 예정

## Situation

`#246`+`#255`는 룸 공유 파일을 `<shared-context>` system-prompt 블록으로 모든 엔진에 일관 전달하는 라인을 복구했지만, `#255` claude 실증에서 한 가지 추가 결함이 드러났다 — 사용자가 `memory/shared/<파일>` 같은 경로 표현으로 묻는 순간 도구-기반 엔진(codex/claude-code/gemini-cli)이 본능적으로 Read 도구를 호출한다. agent cwd는 `<agent_root>/workspace/`이고 canonical 디렉토리는 그 한 단계 위라 sandbox에서 보이지 않아 "파일이 존재하지 않습니다"로 실패. system prompt에는 데이터가 박혀 있어도 모델은 도구 결과를 우선 신뢰하는 경향이 있어 사용자 경험이 깨졌다.

## Task

- `<agent_root>/workspace/memory/shared/`에 canonical 디렉토리를 가리키는 브릿지를 만들어 도구 호출 경로를 살린다.
- daemon의 동적 fan-out(`agent_memory_shared_file_write/delete`)이 별도 동기화 코드 없이 자동 반영되어야 한다.
- raw-SDK 어댑터(anthropic/openai/openhands/deep_agents)는 Read 도구가 없으므로 브릿지를 만들 이유가 없다 — 그쪽엔 영향 0.
- prompt 안내 문구도 양쪽 채널을 동등하게 다룬다고 명시해 모델 혼동을 줄인다.

## Action

- `packages/machine/doorae_machine/spawner.py:608-647` (`_materialize_agent_dir` 끝) — `.claude` 브릿지 직후에 `workspace/memory/shared` 슬롯 처리 블록 추가. 매 spawn마다 stale link/file/dir을 unlink/rmtree로 청소 후, `msg.engine in ("codex", "claude-code", "gemini-cli")`면 `workspace/memory/`를 `mkdir(parents=True, exist_ok=True)`로 보장하고 `ws_shared.symlink_to("../../memory/shared")`. raw-SDK 엔진은 슬롯을 부재 상태로 둔다.
- `packages/agent/doorae_agent/memory/compose.py:87-93` — `_SHARED_CONTEXT_GUIDE`에 "파일시스템 도구가 있는 엔진이라면 동일 내용을 `memory/shared/<파일명>` 경로로도 Read 가능합니다 — 이 블록과 도구 결과는 같은 바이트입니다(읽기 전용)." 한 줄 추가.
- `packages/machine/tests/test_materialize.py:1018-1108` — `TestWorkspaceSharedBridge` 신설. 7 케이스: 엔진별 symlink 모양(parametrize codex/claude-code/gemini-cli), raw-SDK 미생성, 사후 daemon write가 브릿지로 보이는지, respawn 멱등성, 엔진 스왑 시 stale 청소.

## Decisions

`.tmp/plan-257-workspace-shared-bridge.md`의 세 결정을 코드에 반영.

- **브릿지 형태 = 디렉토리 1개 symlink**. file-by-file symlink는 spawn 시점에 디렉토리가 비어 있어 본질적으로 무효(daemon이 런타임에 동적으로 채움); real-copy + daemon dual-write는 daemon 표면을 늘리고 멱등성 책임을 둘로 쪼갬. AGENTS.md 브릿지가 같은 read-OK / write-blocked-by-sandbox 모델로 이미 운영 중이라 위험 동등.
- **gemini-cli도 같은 분기에 통일**. AGENTS.md에서 거부됐던 건 *file* symlink였고 *directory* symlink는 시도해본 적 없음. T4 수동 실증에서 거부 확인되면 gemini만 daemon dual-write로 후속 분리. 코드 예측보다 실측 우선.
- **raw-SDK 엔진은 분기에서 제외**. `grep tool_use` 0건이라 브릿지를 만들어도 호출자 없음. deep_agents 등이 향후 도구를 가지면 그 시점에 분기 한 줄 추가로 해결 — YAGNI.
- **prompt guide 보강을 같은 PR에 묶음**. 브릿지만 추가하면 raw-SDK는 system prompt만, 도구 엔진은 도구만 보는 비대칭이 생기고, 사용자 실증 시점에 "지금 너의 system prompt에 있어?"-"Y"-"그럼 보여줘"-"안 보임" 같은 모순 답변이 또 발생. guide 한 줄로 양 채널을 동등 인지시킴.
- **미해결 / 재검토 트리거**: gemini-cli sandbox가 cwd 안 디렉토리 심볼릭(target outside cwd)의 file read를 허용하는지 미실측. 거부 시 gemini만 후속 PR로 분리.

## Result

- `TestWorkspaceSharedBridge` 7 케이스 모두 GREEN.
- 회귀: machine 311 / agent 274 / cluster 737 통과(e2e_materialize는 cluster 단독 import 실패로 기존부터 deselect).
- 사용자 기대 경로: claude/codex agent에게 "memory/shared/<파일> 보여줘" → Read 도구가 sandbox 안 `workspace/memory/shared/<파일>`을 symlink 따라 canonical 위치에서 읽어 성공 응답. 도구 없는 raw-SDK 엔진은 변함없이 `<shared-context>` 시스템 프롬프트 블록으로 응답.
- 후속 작업: T4 수동 실증(테스트룸2에서 claude/codex/gemini agent 모두 직접 도구 호출 시나리오 검증). gemini가 디렉토리 심볼릭을 거부하면 별도 이슈로 분리.
