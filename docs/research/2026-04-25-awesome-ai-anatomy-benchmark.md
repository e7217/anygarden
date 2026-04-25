# doorae × awesome-ai-anatomy 비교 분석 보고서

> Date: 2026-04-25
> Branch: `research/awesome-ai-anatomy-benchmark`
> Source: NeuZhou/awesome-ai-anatomy (16 teardowns + CROSS-CUTTING.md)
> Plan: `.tmp/plan-awesome-ai-anatomy-benchmark.md`
> Companion: `docs/research/doorae-meta.yaml` (META_SCHEMA self-mapping)

---

## 1. 요약

awesome-ai-anatomy 레포의 16개 AI 에이전트 오픈소스 teardown 중 doorae와 가장 가까운 프로젝트는 **oh-my-claudecode**(Claude Code 호스트, 19-agent team)와 **oh-my-codex**(Codex CLI 호스트, 30-agent worker farm)다. 그러나 doorae는 두 프로젝트와 한 가지 결정적 차원에서 다르다 — **7개 외부 코딩 에이전트 CLI(anthropic, claude_code, codex, deep_agents, gemini_cli, openai, openhands)를 _평등하게_ 호스팅**한다. 두 비교 대상은 모두 단일 호스트 CLI에 종속된다.

이 정체성 차이는 doorae의 다른 결정도 설명한다. agent loop 본체는 외부 CLI에 위임(OpenHands처럼 1391줄 god-class를 만들지 않음), 격리는 process boundary + safefs O_NOFOLLOW(Codex CLI의 17K-line 샌드박스를 만들지 않음), 컨텍스트는 per-room policy + ingest gate(OpenHands 10 condenser 파이프라인을 만들지 않음). 11일 신생임에도 16개 중 다수가 빠진 함정(borrowed core, god-file, security-as-README, env-var-gated permission)을 의식적으로 회피한 신호가 코드에 남아 있다.

다만 verify 결과 **세 가지 _현재_ 위험**이 식별됐다: (1) MCP tool call audit log 부재, (2) cost/step ceiling 부재, (3) 800줄 초과 모듈 9개의 god-file 형성 임계점 접근. 다음 6개월의 핵심 결정은 이 세 위험에 대응하는 **TIER 1 액션 2개 + 코드 변경 0의 PR 정책 1개**로 압축된다.

## 2. doorae 자기 매핑 요약

상세는 `docs/research/doorae-meta.yaml` 참조. 주요 차원:

| 차원 | 값 | 비고 |
|---|---|---|
| Category | coding-agent (host) | META_SCHEMA enum과 정확히 안 맞음 — "host of coding-agent CLIs"로 정정 |
| Loc | 88,397 (Python 62K + TS 26K) | 11일 신생 (첫 커밋 2026-04-14) |
| License / Deployment | Apache-2.0 / pip (no Docker) | PyPI 3개 패키지 + npm 1개 |
| `agent_loop` | custom (engine-loop-as-adapter) | 본체는 외부 CLI에 위임 |
| `sandbox` | process_isolation | KILL_TIMEOUT=10s + safefs O_NOFOLLOW + path validation |
| `context_management` | custom (no condenser) | per-room window + agent opt-out, DB migrations 022/023/028 도메인 관통 |
| `stuck_detection` | true | 2-layer (counter brake + 80 LOC content-hash cycle guard, $47K 루프 사례) |
| `multi_agent` | true | WebSocket + machine_bus + speaker strategy + handoff(#159) + TokenBucket cooldown |
| `mcp_support` | true | mcp/{auth, router, tools}.py + Fernet 암호화 |
| `plugin_system` | true | skill_library: github 등록(#119) + agent self-authoring(#120) + approve+audit(#125) |
| `security.layers` | 4 | JWT + multi-token capabilities + Fernet at-rest + FastAPI ACL |
| `providers.count` | 7 | anthropic, claude_code, codex, deep_agents, gemini_cli, openai, openhands |

## 3. 정밀 3개 비교 (요약 표)

전체 추출 노트는 부록 A 참조.

| 차원 | oh-my-codex | oh-my-claudecode | OpenHands | doorae |
|---|---|---|---|---|
| **포지션** | Codex CLI 호스트 | Claude Code 호스트 | Agent loop owner | **멀티 엔진 호스트 (유일)** |
| **격리** | per-task git worktree | host plugin (격리 없음) | Docker per session | subprocess + safefs |
| **IPC** | tmux + dispatch queue | JSONL 파일 + mkdir lock | EventStream | WebSocket + machine_bus |
| **Stuck detection** | bounded fix loop (3 attempts) + autoresearch noop counter | (없음) | 487-line StuckDetector + recovery action | 80-line content-hash + counter brake (recovery 없음) |
| **Plugin 격리** | Process-isolated `.mjs` + 1.5s timeout + SIGTERM/SIGKILL | (없음) | (없음) | (없음 — A1 후보) |
| **Model routing** | (단일 CLI) | Static role→tier (`AGENT_MODEL_MAP`) | (단일 loop) | LLM Gateway + 정적 catalog (역할별 tier 없음 — A2 후보) |
| **Context mgmt** | (없음) | (위임) | 10 condenser pipeline | per-room policy + ingest-only |
| **Security layers** | (단일 머신 trust) | (host 의존) | 3-layer (행동 단위) | 4-layer (진입 단위) |

**3개 비교의 가장 큰 분기점**: 컨텍스트 관리 책임 위치 — OpenHands는 자체 condenser 파이프라인을 가지고, doorae는 호스트로서 외부 CLI에 위임하면서 룸 단위 ingest 정책만 책임. **둘 다 정당** (멀티 에이전트 룸이라 "누가 뭘 보는가"가 압축보다 핵심 문제).

**즉시 보강 가치 큰 패턴 4개** (정밀 3개에서):
- (OpenHands) Error-action cycle detection 추가 → cycle_guard에 ~50줄
- (OpenHands) Condensation/ingest 결정의 EventStream 화 → 디버깅성
- (OMX) Plugin Hook SDK pattern (process boundary + timeout escalation) → skill_library 격리
- (OMC) Static role→tier model mapping → catalog에 컬럼 추가

## 4. 보조 2개 발췌 (요약 표)

전체 추출 노트는 부록 B 참조.

### Cline (60K stars, providers/hooks/YOLO 영역만)

| 영역 | 발견 | doorae 영향 |
|---|---|---|
| **43 providers** | 300줄 switch + provider별 클래스 폭증, 카탈로그 = 코드 | doorae 7 (LiteLLM gateway + 검증된 host CLI)가 **유지비 측면에서 합리** — 의도적 분기, doorae가 옳음 |
| **Common `ApiStream` 컨트랙트** | text/reasoning/tool_calls/usage 표준 chunk | A5 후보 (S) |
| **8 hooks system** | `.cline/hooks/` shell script auto-discovery, sandboxing 부재 | **반면교사** — 도입 전 권한 모델/서명 설계 필수, lifecycle event 표준화 후 hook 진입점 |
| **YOLO mode** | `CommandPermissionController.ts`가 강력하지만 env-var-gated → 무력화 | **doorae #134 MCP 자동승인 재평가** — 안전판은 디폴트 ON 아니면 의미 없음 |

### Hermes Agent (26K stars, skills/memory 영역만)

| 영역 | 발견 | doorae 영향 |
|---|---|---|
| **Self-improving skills** | Voyager + Reflexion 합성, `patch` action + 보안 스캔 | **PR-style diff 제안 + 자동 스캔**을 doorae #125 위에 추가 (검토 후) |
| **Frozen memory snapshot** | BuiltinMemoryProvider가 system prompt block을 세션 시작 시점에 동결, prompt cache hit 향상 | **A7 후보** — 단, 외부 CLI 의존 (~30 LOC) |
| **Agent 자율 in-place patching** | (Hermes 보유) | doorae는 의도적으로 **안 가져감** — admin approve/audit 게이트와 정면 충돌 |

## 5. 횡단 인사이트 (CROSS-CUTTING.md, 10개 프로젝트)

전체 보강 노트는 부록 C 참조.

### 새 Lens 2개 (정밀 5개에서 못 본 차원)

- **Lens 4 — "Loop owner ≠ Cost owner"**: doorae cycle_guard는 _structural_ 루프만 잡고 _economic_ 폭주(같은 메시지 아니지만 GPT-5에 매번 50K 토큰)는 못 잡음. 10개 중 Dify만 cost ceiling 보유. doorae는 7 어댑터 호스팅이라 단일 폭주가 룸 비용 합산으로 전이 → **Dify보다 더 절실**.
- **Lens 5 — "Borrowed core blast radius"**: doorae는 코어 loop를 자기 소유로 OMC 함정은 피했으나 **어댑터 단위로 borrowed core가 7배**. A8 contract test가 단순 회귀가 아니라 blast radius 관리 도구.

### 횡단에서만 떠오른 액션 6개

- **A9** Cost / Step ceiling layer (Dify ExecutionLimitsLayer)
- **A10** Order-independent hash loop (DeerFlow warn@3 / kill@5)
- **A11** Declarative JSON provider config (Goose)
- **A12** Memory write file-locking (DeerFlow flat-file corrupt 사례)
- **A13** Middleware ordering의 dependency declaration (DeerFlow 14-middleware 함정)
- **A14** Sub-agent depth=2 + tool-restriction-per-depth (Dify만 보유)

### CROSS-CUTTING 빈도가 가리키는 우선순위 신호

- "Loop detection from day 1": 10개 중 5개 보유 → A3/A10 high priority
- "Cost budgets": 10개 중 1개 보유 → **A9 신설이 단일 최대 격차**
- "Borrowed core loop": 10개 중 4개 함정 → A8 contract test fuzz 확장
- "Frozen prompt cache snapshot": 10개 중 1개(Hermes) → A7 저비용·고효과 비대칭

## 6. 갭 분석 — 액션 아이템 (verify 반영 최종)

각 항목의 상세 도출 근거는 부록 A~D 참조. **verify 결과 조정된 우선순위**:

### TIER 1 (즉시-필수, 1~2주 내)

#### LIFTED-1. MCP tool call audit log 신설 ← _verify로 격상_

- **현재 상태**: `mcp/router.py` + `mcp/tools.py`에 `audit` 키워드 0건. doorae 측 로깅 없음. SDK 자체 로그에만 의존.
- **위험**: agent가 어떤 MCP tool을 언제 호출했는지 추적 불가. 사고/오작동 발생 시 사후 분석 불가, compliance 시나리오 회피 불가.
- **비대칭 결정적 증거**: skill 등록은 `skill_library_audits` 테이블로 모든 액션 기록(#125), 같은 도메인의 MCP tool call은 무로깅.
- **권고**: `skill_library_audits` 패턴을 `mcp_tool_audits`로 복제. `mcp/router.py:mcp_rpc`에 wrap + 새 테이블 + alembic migration + 테스트.
- **비용**: S~M (~100~200 LOC).
- **출처**: Cline 반면교사 + verify 발견.

#### A9. Cost / Step ceiling layer

- **현재 상태**: `llm_gateway/usage_logger.py`가 Anthropic/OpenAI/SSE 모두 parse + DB persist. **인프라 절반 이미 존재**. 빠진 건 한도 컬럼 + grace stop 경로 + UI + Prometheus metric.
- **위험**: 7 어댑터 동시 호스팅에서 한 어댑터 폭주가 룸 비용 합산으로 전이. cycle_guard는 structural 루프만 잡음.
- **권고**: per-room/per-agent에 누적 토큰·요청수·달러·wall-clock 한도. 한도 초과 시 cycle_guard와 동일한 grace stop 경로. 사용자 인상 요청 워크플로(approve-style).
- **비용**: S~M (~40~80 LOC + DB 컬럼 + UI).
- **출처**: CROSS-CUTTING §6 Anti-Pattern 2 + Dify ExecutionLimitsLayer.

### TIER 2 (즉시-가성비, 1~2개월)

#### A3 + LIFTED-2. Cycle guard 강화 + recovery action ← _verify로 통합_

- **현재 상태**: cycle_guard.py 80줄, content-hash만 봄, **brake only — recovery action 없음**.
- **권고 (3 부분)**:
  - **A3 Error-action cycle 감지**: 같은 tool error → 같은 fix → 같은 error 패턴 (~50줄)
  - **LIFTED-2 Recovery action**: 발화 시 단순 drop이 아니라 "approach 변경" 메시지 자동 발송 옵션
- **비용**: M
- **출처**: OpenHands StuckDetector + verify 발견.

#### A10. Order-independent hash loop detection

- **권고**: tool-call argument 정렬 후 hash, "warn@3 / kill@5" 2단 임계. 기존 cycle_guard와 공존.
- **비용**: S (~30~50줄).
- **출처**: DeerFlow §7 권고.

#### A7. Frozen system-prompt snapshot

- **현재 상태**: spawner.py:50, 350~365에서 spawn 시점에 DB의 `memory_md` → `notes.md` 파일로 쓰고, 파일을 system prompt에 박는 건 **외부 CLI(claude/codex)에 위임**. doorae 자체는 anchoring 메커니즘 없음.
- **권고**: agent integration의 system prompt 조립 부분에 불변 사본 캐시. 매 spawn마다 같은 입력 → 같은 system prompt → 외부 CLI prompt cache hit 향상.
- **주의**: 효과는 외부 CLI의 prompt cache 동작에 의존. 도입 전 LLM gateway의 cache hit metric 측정 필요.
- **비용**: S (~30 LOC).
- **출처**: Hermes BuiltinMemoryProvider.

#### R1 예방. PR 리뷰 체크리스트 — 800줄 cap

- **현재 상태**: 800줄 초과 모듈 9개, 1000줄 초과 6개 (rooms/router.py 1433줄 최다).
- **권고**: 코드 변경 0의 정책 도입 — "단일 파일 800줄 초과 시 분해 검토 의무" PR 리뷰 체크리스트.
- **비용**: 정책 변경만 (CONTRIBUTING.md 또는 PR 템플릿 수정).
- **출처**: CROSS-CUTTING §6 Anti-Pattern 1 + verify LoC 측정.

### TIER 3 (중기-방어, 3개월)

#### A8 + R3. 어댑터 contract test fuzz 확장

- **현재 상태**: `tests/test_integrations/`에 7 adapter + delegate + room_query + should_respond 각각 테스트 보유 (smoke 수준).
- **권고**: 단순 smoke가 아니라 **schema fuzz** — 잘못된 stdout/stderr를 주입해 어댑터가 코어로 손상된 데이터를 흘리지 않는지 검증. catalog refresh 자동화도 묶음.
- **비용**: M.
- **출처**: OMC 위험 교훈 + Lens 5 (borrowed core blast radius).

#### A1 + A13. Plugin 격리 + middleware ordering 메타

- **A1**: skill/MCP 등 user-extensible 코드를 process boundary로 격리 (OMX Hook SDK 패턴).
- **A13**: cycle_guard·permission·MCP autoswitch·cooldown 등 인터셉터에 `before=[...]`, `after=[...]` 메타 부여, 부팅 시 topological sort.
- **결합 시너지**: middleware ordering 없이 plugin 격리만 하면 새 가드 끼울 때마다 손으로 순서 조정.
- **비용**: M (A1) + S (A13)
- **출처**: OMX + DeerFlow + Goose.

### TIER 4~6

| Tier | 액션 | 비용 | 발화 트리거 |
|---|---|---|---|
| 4 | A2 Static role→tier model mapping | S | 룸 lead+보조 패턴 정착 후 |
| 4 | A11 Declarative JSON provider config | M | 30+ provider 로드맵 발생 시 |
| 5 | A4 EventStream-based ingest decision | M | 룸 컨텍스트 디버깅이 진짜 문제될 때 |
| 5 | A5 API stream contract 표준화 | S | 커스텀 어댑터 비표준 chunk 문제 시 |
| 5 | A6 UserPromptSubmit + context injection hook | M | A1 도입 후 안전판 확보 |
| 6 | A12 Memory write file-locking | S~M | _실제_ race 시나리오 발생 시 (현재 transaction으로 OK) |
| 6 | A14 Sub-agent depth=2 tool restriction | S | 첫 multi-hop handoff PR |

### 위험 4개 — 액션과의 관계

| 위험 | 등급 | 대응 |
|---|---|---|
| **R1 God-file 형성** | 중→고 (verify) | TIER 2 PR 정책 + A13 자연 분해 압력 |
| **R2 Security as README** | 중~고 | LIFTED-1 (MCP audit) — 직접 대응. **신규 보안 PR에 "code enforcement evidence" 라벨** |
| **R3 Borrowed core blast radius** | 고 | A8 fuzz 확장 |
| **R4 Long-running room context overflow** | 중 | A4 lossless 단계만 먼저 (사용 패턴 보고 결정) |

## 7. 검증 노트

이 보고서의 모든 doorae 측 주장은 **2026-04-25 시점 코드와 대조 검증** 완료. awesome-ai-anatomy 측 주장은 1차 인용일 뿐 — **도입 결정 시점에 원본 소스 직접 verify 필수**.

### Phase D.5 verify 결과 요약

**확인된 사실**:
- ✅ MCP `_create_skill` → `service.create_from_agent` → `skill_library_audits` 자동 기록 (R2-a OK)
- ✅ `skill_library_audits` 테이블 + 6개 action(register/approve/reject/delete/attach/detach) 모두 기록 + grandfather migration 안전 (R2-b 모범 사례)
- ✅ 7 어댑터 + delegate + room_query + should_respond 각각 contract test 보유 (R3 부분 완화)
- ✅ `usage_logger.py`가 Anthropic/OpenAI/SSE 전부 parse + DB persist (A9 인프라 절반)
- ✅ `safefs.py` O_NOFOLLOW로 symlink 우회 차단 + Limitations docstring 명시 (worth_stealing 추가)
- ✅ skills_library/service.py의 모든 mutation에 `await db.commit()` 일관 (A12 transaction OK)

**확인된 갭**:
- ❌ MCP tool call에 audit log 0건 (LIFTED-1)
- ❌ token usage 한도/grace stop 경로 0개 (A9)
- ❌ cycle_guard recovery action 0개 (LIFTED-2)
- ❌ machine `supervisor.py`에 stale reaper / heartbeat 0개 (후순위)
- ⚠️ 800줄 초과 모듈 9개 (R1 격상)

### 도입 결정 시점에 추가 verify 필요한 항목

| 액션 | verify 항목 | 위치 |
|---|---|---|
| A7 Frozen snapshot | 외부 CLI의 prompt cache hit 효과 측정 | `llm_gateway/usage_logger.py` 통계 |
| A9 Cost ceiling | 초기 한도값 — 프로덕션 사용량 분포 | DB usage rows + Prometheus |
| A14 Depth restriction | 현재 handoff가 실제 몇 hop까지 가는지 | worklog #159 + 운영 데이터 |
| A12 동시 write | 룸 shared file 동시 편집 빈도 | worklog 246/255/257 운영 데이터 |
| LIFTED-2 Recovery | cycle_guard 발화 빈도와 falsy positive | structlog + `agent/integrations/cycle_guard.py` 호출자 |

### awesome-ai-anatomy 저자 주장 중 도입 전 검증 권장

- OMX "Plugin Hook SDK 1.5s timeout / SIGTERM 250ms / SIGKILL" — 실제 timeout 값이 doorae MCP 요청 분포에 적합한지
- OMC "Heartbeat 5분 stale + 25→500ms exponential backoff" — doorae machine 데몬 환경에 적합한 임계값
- OpenHands "487-line StuckDetector recovery 로직" — `_handle_loop_recovery_action`이 doorae에 가져갈 만큼 일반화 가능한지
- Cline "300줄 switch는 maintainability 낙후" — 7개 어댑터 수준에서는 trade-off가 다를 수 있음
- Hermes "Frozen snapshot이 prompt cache 30~50% 절감" — 자체 측정값 미공개, doorae가 직접 측정 후 결정

### 보고서의 한계

- awesome-ai-anatomy 16개 중 정밀 3 + 보조 2 + 횡단 분석에서 추가 5개(MiroFish, Dify, DeerFlow, Goose, Guardrails) = 10개에 한정. 나머지 6개(Browser Use, Lightpanda, MemPalace, Pi Mono, Hermes Agent와 부분 겹침, ...)는 표면만 본 상태.
- doorae 측 verify는 _코드 grep + 핵심 파일 read_ 수준. 동시성 race, 보안 취약점, 성능 병목의 정밀 측정은 별도 작업 필요.
- "도입 비용 S/M/L"은 LoC 기반 추정. 실제 PR 단위로는 테스트·문서·UI까지 포함해 1.5~2배 가능.

---

## 부록 A — 정밀 3개 추출 노트 (전문)

### A.1 oh-my-codex

> Source: `oh-my-codex/README.md` (fetch 2026-04-25), meta: dev-tool / TypeScript 124K LOC / no license
> Key finding: "Codex CLI 위에 30 에이전트, 5-phase team pipeline, git worktree per worker, hook plugin SDK, autoresearch 자율 실험 루프"

**훔칠 패턴**:
1. **Plugin Hook SDK** — `src/hooks/extensibility/dispatcher.ts` (RUNNER_SIGKILL_GRACE_MS=250, RESULT_PREFIX="`__OMX_PLUGIN_RESULT__ `"), `loader.ts` (timeout 1500ms). 플러그인을 자식 프로세스로 spawn → stdin JSON envelope → stdout magic prefix → 1.5s timeout → SIGTERM → 250ms 유예 → SIGKILL. SDK는 tmux/log/state/omx 4 namespace로 read-only. **doorae A1 직접 참고**.
2. **Heuristic Task-to-Worker Allocation** — `allocation-policy.ts` `scoreWorker()` (role match +18/+12/+9, overlap *4, negative-overlap -3, load -4). LLM 호출 0회, microsecond. Specialization이 emergent.
3. **Phase-Gated Pipeline with Bounded Fix Loops** — `orchestrator.ts` TRANSITIONS map, max fix attempts 3. `verify→fix` attempt 카운터 한도 초과 시 `failed`.
4. **AutoResearch Loop with Keep/Discard Ledger** — `autoresearch/runtime.ts`. 매 iteration commit hash 저장 → agent 작업 → evaluator → 점수 개선 시 keep, 아니면 `git reset --hard`. trailing-noop 카운터로 stuck detection.

**안티패턴**:
- License 누락 (19K stars, GitHub API license: null) — doorae는 Apache-2.0 명시로 회피.
- 30 agent × 5 phase 디버깅 비용 — 저자 본인 인정. doorae는 observability plan(`docs/plans/2026-04-20-agent-observability-design.md`)에 phase/turn ID를 envelope에 박는 설계 미리 반영 권장.

**결정 분기점**:
- Worker 격리: OMX per-task git worktree ↔ doorae subprocess+safefs. doorae 옳음(채팅 단위 협업).
- Plugin 격리: OMX process+timeout ↔ doorae in-process MCP boundary. **A1 후보**.
- Stuck detection 범위: OMX phase/code 단위 ↔ doorae message/turn 단위. doorae 옳음(채팅 도메인).
- 작업 라우팅: OMX heuristic ↔ doorae 호명 기반. doorae 자동 디스패치 도입 시 OMX 패턴 참고.

**한 줄 종합**: 가장 큰 분기는 worker 격리 단위, doorae 즉시 도입은 process-isolated plugin SDK with timeout escalation.

### A.2 oh-my-claudecode

> Source: `oh-my-claudecode/README.md` (fetch 2026-04-25), meta: dev-tool / TypeScript 194K LOC / MIT
> Key finding: "19-agent team via file IPC, mkdir-based locking, Haiku→Opus model tier routing, tri-model coordination"

**훔칠 패턴**:
1. **Prompt-as-Markdown** — `/agents/*.md`. `fs.readFileSync(./agents/${agentRole}.md, "utf-8")`. 비엔지니어도 배포 없이 prompt 튜닝.
2. **Static Model Tier Routing** — `AGENT_MODEL_MAP` (critic→haiku, codeReviewer→opus, planner→sonnet). 30~50% 토큰 절감 주장(저자, 자체 측정 미공개).
3. **Heartbeat + Stale Lock 5분 정리** — `LOCK_STALE_MS = 5 * 60 * 1000`, `workers/worker-N/heartbeat.json`, exponential backoff 25→500ms.
4. **Phase Controller가 task status 분포로 phase 추론** — 명시적 FSM 없이 pending/in_progress/completed/failed 카운트. `metadata.permanentlyFailed=true`로 false-success 방지.

**안티패턴**:
- Host CLI internals에 plugin으로 강결합 — Claude Code minor release 한 번이 19-agent 시스템 전체 깨뜨릴 수 있음. doorae는 subprocess wrap이라 결합도 ↓, 다만 7 어댑터 동시 깨짐 위험 (R3).
- JSONL 파일 IPC를 채팅에 일반화 금지 — polling 지연이 채팅 UX엔 부적합.

**결정 분기점**:
- IPC 매체: OMC 파일/JSONL+mkdir lock ↔ doorae WebSocket+machine_bus. doorae 옳음(UX 1차).
- 모델 라우팅 위치: OMC 코드 내 정적 ↔ doorae catalog+gateway. doorae 옳음, 단 역할별 tier 권장값 보완 여지(A2).
- Agent loop 소유권: OMC host plugin ↔ doorae 외부 CLI 위임. doorae 옳음(멀티 엔진 평등).
- Prompt 저장소: OMC `/agents/*.md` ↔ doorae 코드/카탈로그. **재검토 가치 — admin UI에서 prompt 편집 + git 백업**.

**한 줄 종합**: 가장 큰 분기는 IPC 매체, doorae 즉시 도입은 catalog에 `recommended_tier_per_role` 컬럼.

### A.3 OpenHands

> Source: `openhands/README.md` (fetch 2026-04-25), meta: coding-agent / production / docker / Python+TS 400K / 71K stars / MIT
> Key finding: "10 condensers, 3-layer security, 487-line stuck detector, V0/V1 active migration"

**훔칠 패턴**:
1. **Condensation을 EventStream의 1급 이벤트로** — `RollingCondenser.should_condense / get_condensation`, return `View | Condensation`. 언제·왜 압축됐는지 history에 남음.
2. **AmortizedForgettingCondenser** — 69줄. 하드 cutoff 대신 나이별 지수 감쇠 생존확률. 가벼운 옵션.
3. **Tool로서의 voluntary condensation request** — `CodeActAgent`의 `CondensationRequestTool`. 효과 벤치 미공개(저자).
4. **StuckDetector 1급 클래스 + 전용 테스트** — `controller/stuck.py` 487줄, `TestAgentControllerLoopRecovery` 409줄. 반복 액션, error→fix→error, syntax loop, interactive vs headless 분기. `StuckAnalysis` 데이터클래스 + `LoopDetectionObservation` emit + `_handle_loop_recovery_action`.

**안티패턴**:
- AgentController 1391줄 단일 클래스 (저자도 candidate for decomposition 명시). doorae는 분리되어 있어 위험 ↓.
- V0/V1 동시 운영 + 통과한 deprecation 데드라인 (April 1, 2026). doorae는 7 어댑터로 누적 위험 — **deprecation hard date에 CI 게이트** 필요.

**결정 분기점**:
- Context 관리: OpenHands 10 condenser pipeline ↔ doorae per-room policy + ingest-only. **둘 다 정당** (멀티 에이전트 룸이 압축보다 "누가 뭘 보는가" 핵심).
- Sandbox: OpenHands Docker per session ↔ doorae subprocess + safefs. doorae 위협 모델 적합.
- 보안 layer 모델: OpenHands 행동 단위 3-layer ↔ doorae 진입 단위 4-layer. doorae 옳음(host 위협), 다만 행동 단위 평가 0개라 잠재 갭.
- 어댑터 vs 자체 loop: OpenHands 6개 자체 agent ↔ doorae 7개 외부 CLI 어댑터.

**Stuck detection 비교 (특별 주목)**:
- **공통점**: 둘 다 retry counter 넘어 패턴 기반, 둘 다 1급 모듈로 분리.
- **차이점**: (1) OpenHands 액션 시퀀스/에러 타입, doorae 메시지 콘텐츠 해시 — doorae 신호 가볍지만 "다른 메시지 같은 의미", "같은 에러 사이클" 미감지. (2) OpenHands 감지 후 recovery action까지, doorae brake 위주 (LIFTED-2). (3) interactive vs headless 분기 OpenHands에 있고 doorae엔 없음.
- **즉시 가져갈 한 가지**: error-action cycle 감지를 cycle_guard에 추가 (~50줄). content hash와 직교 신호.

**한 줄 종합**: 가장 큰 분기는 컨텍스트 관리 책임 위치, doorae 즉시 도입은 Condensation/ingest 결정의 EventStream 화로 디버깅성 ↑.

## 부록 B — 보조 2개 발췌 (전문)

### B.1 Cline (60K stars, providers/hooks/YOLO)

**1. 43 Provider 시스템**
- `src/core/api/providers/` 43개 1:1 파일.
- `ApiHandler` (L186-192): `createMessage`, `getModel`, `getApiStreamUsage?`, `abort?`. 공통 `ApiStream` async generator(text/reasoning/tool_calls/usage).
- `buildApiHandler`: 300+ line switch.
- Plan/Act 이중 모드: factory가 `mode` 파라미터로 다른 API key·model·thinking budget.
- 유지 비용: "competitive moat" + "각 provider distinct class with own SDK import".
- doorae 비교: 7 vs 43, 분기 의도적. doorae 가져갈 패턴: 공통 `ApiStream` 컨트랙트(A5, S), Plan/Act 모드별 모델 분리(검토 후, M). 피할 함정: 300줄 switch + 클래스 폭증.

**2. Hooks System**
- `src/core/hooks/`, `hook-executor.ts` (L274). 8 훅 타입 (`TaskStart`, `TaskResume`, `TaskCancel`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PreCompact`, `Notification`). 5개 cancellable.
- `.cline/hooks/` shell script auto-discovery, `HookFactory` config, `HookProcess` spawn, `executeHook` lifecycle + `AbortController`.
- Context injection: cancellable hook 반환 시 `<hook_context source="HookName">...</hook_context>` 주입.
- 안전성: VS Code 권한 실행 → 임의 코드 실행 가능, sandboxing/서명 검증 없음(저자 경고).
- doorae 비교: lifecycle 이벤트(`scheduler/lifecycle.py`) 있으나 user-defined shell hook 부재. 가져갈 만한 훅: `UserPromptSubmit + context injection`(M, 검토 후), `PreCompact`(S, 검토 후). **도입 시점: 지금 부적절** — sandboxing 부재가 약점, 처음부터 권한 모델/서명 설계.

**3. YOLO Mode**
- `autoApprove.ts`, 토글 `yoloModeToggled` (L229).
- `shouldAutoApproveTool`이 모든 tool에 `[true, true]` — `execute_command` 포함, 모든 permission logic 우선 실행.
- 안전판: `CommandPermissionController.ts`가 `CLINE_COMMAND_PERMISSIONS` 환경변수로 allow/deny glob. **저자 인용**: "the most security-conscious code in the entire codebase — and it's gated behind an environment variable that almost nobody will set" (L231).
- doorae 비교: #134는 MCP에 한정, sandbox 유지. Cline YOLO는 shell 포함. doorae 더 보수적. **재평가 가치**: (1) 자동 승인 토글 자체를 logging/감사(LIFTED-1), (2) command/shell tool은 별도 deny-list controller로 분리(M, 검토 후).

**한 줄 종합**: 즉시 보강은 MCP #134에 audit log + per-tool deny-list, 의도적 분기는 provider 다중성 7 vs 43.

### B.2 Hermes Agent (26K stars, skills/memory)

**1. Self-Improving Skills**
- Voyager + Reflexion 합성(저자 주장).
- 자동 skill 생성: 복잡한 작업 종료 시 `~/.hermes/skills/<name>/SKILL.md` 자동 생성. markdown runbook (Voyager의 JS → markdown 차이).
- `skill_manager_tool.py` 6 actions: create/edit/patch/delete/write_file/remove_file. **patch는 targeted find-and-replace** — 부분 갱신.
- 검증 사이클: 생성/patch 직후 security scan (prompt injection 차단).
- 개선 트리거: 사용 중 "더 나은 방법" 발견 시 자리에서 patch (Reflexion verbal feedback → 코드 수정).
- doorae 비교: doorae #119-126은 사람 게이트(github 등록 → admin approve → audit), Hermes는 자동 사이클. **즉시 가져갈 (S, 검토 후)**: patch action의 targeted find-and-replace + post-edit security scan을 doorae admin approve 위에 "diff 제안 + 자동 스캔 후 admin 승인" 단계로. **검토 후 (M)**: 작업 종료 시 자율 트리거 자동 제안. **도입 전 결정**: admin approve와 자기 patch 충돌 → 자동 patch도 PR-style로 제안 후 admin merge가 일관성 ↑.

**2. Frozen Memory Snapshots**
- `BuiltinMemoryProvider`가 세션 시작 시 MEMORY.md/USER.md 읽어 system prompt block snapshot으로 고정. 세션 중 file 변경되어도 system prompt 불변.
- 트리거: 세션 로드 시 1회 (load time).
- 1차 목적: prompt-cache 최적화. 4K 단어 MEMORY.md에서 매 memory write마다 system prompt 재컴파일 회피.
- 저장: in-memory snapshot (별도 영속 파일 아님, 추정).
- doorae 비교: spawner.py:50 `memory_md` 필드 + 350~365 spawn 시 notes.md 쓰기. agent가 자유롭게 덮어씀. **system prompt 박는 건 외부 CLI 위임**. **시나리오 1 (S, 즉시)**: 멀티세션 DM(#237)에서 같은 페어 반복 spawn 시 system prompt block 안정화로 LLM gateway prompt cache hit ↑. **시나리오 2 (M, 검토 후)**: room shared files bridging(#246/#255/#257)에서 turn 중간 컨텍스트 변경 비결정성 제거.
- 도입 비용: ~30 LOC. 단, 외부 CLI prompt cache 동작에 의존 — 도입 전 측정 필요.

**한 줄 종합**: 즉시 가져갈 것은 frozen system-prompt snapshot, 의도적으로 안 가져갈 것은 agent 자율 in-place skill patching (admin approve와 충돌).

## 부록 C — CROSS-CUTTING 횡단 보강 (전문)

> Source: CROSS-CUTTING.md (fetch 2026-04-25, 385줄, **10개 프로젝트** 횡단)
> 16개 정밀과 부분 겹침 — 추가 5개(Dify, DeerFlow, Goose, Guardrails, MiroFish + Pi Mono, Lightpanda) 신호에 집중

### 새 Lens

**Lens 4 — "Loop owner ≠ Cost owner"**
- 근거: §6 Anti-Pattern 2 — DeerFlow/Hermes/MiroFish/OMC 모두 token tracking은 있지만 **dollar/step ceiling은 없음**. 10개 중 Dify만 execution limits (500 steps, 1200 seconds).
- doorae 적용: cycle_guard와 KILL_TIMEOUT은 있으나 **cost-side ceiling 부재**. 7 어댑터에서 한 폭주가 룸 전체로 전이.
- doorae 위치: deterministic layer를 가졌지만 "loop 방지"에만 쓰이고 "cost 방지"엔 안 쓰임. **2축 가드**(structural cycle + economic budget) 분리 필요.

**Lens 5 — "Borrowed core blast radius"**
- 근거: §6 Anti-Pattern 3 "Borrowed Core, No Ownership" — MiroFish→OASIS, DeerFlow→LangGraph, OMC→Claude Code. §7 "Don't borrow your core loop".
- doorae 적용: doorae는 코어를 자기 소유로 OMC 함정은 피했으나 어댑터 단위로 borrowed core가 7배.
- doorae 위치: A8 contract test가 단순 회귀가 아니라 **borrowed-core blast radius 관리 도구**. host = borrowed core가 N개라는 뜻이며 N이 클수록 contract test 자동화 비중 ↑.

### 새 액션 6개

A9~A14는 §6 Anti-Patterns의 직접 대응:
- A9 Cost ceiling layer (Dify 단독 보유) — TIER 1로 격상
- A10 Order-independent hash loop detection (warn@3/kill@5)
- A11 Declarative JSON provider config (Goose 30+ provider)
- A12 Memory write file-locking (DeerFlow flat-file corrupt 사례)
- A13 Middleware ordering의 dependency declaration (DeerFlow 14-middleware "ClarificationMiddleware MUST be last" 주석 함정)
- A14 Sub-agent depth=2 + tool-restriction-per-depth (10개 중 Dify만 depth>1)

### 새 위험 4개

- **R1 God-file 형성**: 10개 중 6개 god-file 보유 (Hermes 9K, Claude Code query.ts 1.7K, OpenHands 1391). doorae는 신생이라 분기점, middleware/inspector 패턴 + PR 리뷰 체크리스트로 예방.
- **R2 "Security as README notice" 회귀**: DeerFlow/MiroFish/Pi Mono. doorae는 강한 보안 자세에서 출발 → **회귀가 더 위험**. 신규 보안 PR에 "code enforcement evidence" 라벨 의무화.
- **R3 Borrowed core blast radius**: A8 contract test를 schema fuzz로 확장.
- **R4 Memory compression 없이 long-running room 누적**: §2 doorae "Context = custom (no condenser)". 10개 중 6개 4-layer/5-step compression 보유. Hermes 5-step 또는 Claude Code 4-layer의 lossless 단계만 먼저 도입.

### 우선순위 빈도 분석

- "Loop detection from day 1": 10개 중 5개 → A3+A10 high
- "Cost budgets": 10개 중 1개 → **A9 단일 최대 격차**
- "Borrowed core loop": 10개 중 4개 함정 → A8 우선순위 ↑
- "Inspector/middleware pipeline": Goose+DeerFlow 모범 → A1+A13 결합 시너지
- "Frozen prompt cache snapshot": 10개 중 1개(Hermes) → A7 비대칭

## 부록 D — verify 발견 사항 상세

### D.1 코드 측정값

```
Python LoC top 15 (테스트/마이그레이션 제외):
  1433  cluster/rooms/router.py
  1065  cluster/skills_library/service.py
  1063  cluster/db/models.py
   996  cluster/api/v1/agents.py
   993  cluster/ws/handler.py
   990  machine/spawner.py
   855  machine/daemon.py
   739  cluster/scheduler/lifecycle.py
   723  cluster/api/v1/skills.py
   702  cluster/api/v1/graph.py
   696  agent/doorae_agent/client.py
   695  cluster/api/v1/llm_gateway.py
   684  cluster/app.py
   621  agent/doorae_agent/integrations/claude_code.py
```

### D.2 어댑터 contract test 현황

`packages/agent/tests/test_integrations/`:
- test_anthropic.py
- test_claude_code.py
- test_codex.py
- test_deep_agents.py
- test_delegate.py
- test_gemini_cli.py
- test_openai.py
- test_openhands.py
- test_room_query.py
- test_should_respond.py

7 어댑터 + delegate + room_query + should_respond 모두 존재. 수준은 smoke (fuzz 아님).

### D.3 동시성 lock 사용 위치

```
packages/cluster/doorae/scheduler/machine_bus.py
packages/cluster/doorae/llm_gateway/supervisor.py
packages/machine/doorae_machine/daemon.py
packages/cluster/doorae/ws/manager.py
packages/agent/doorae_agent/runtime/handler_wrapper.py
```

`agent_dir.py`, `skills_library/service.py`에는 lock 없음 — DB transaction으로 처리(commit boundary 일관). safefs는 O_NOFOLLOW symlink 방어, atomic write 보장은 없으나 doorae 모델에선 충분.

### D.4 worklog #134 핵심 인용 (MCP 자동승인)

> "agent01-claude / codex agents가 attached MCP 툴을 프롬프트 없이 호출 가능. ... 보안 트레이드오프: agent가 workspace 내부에서 자유롭게 툴 호출. 외부 접근은 MCP 서버 자체가 차단 (filesystem MCP의 MCP_FS_ALLOWED_PATH 등). 시스템 격리는 유지"

→ 자동승인은 의도적 결정. 단 **호출 audit 부재**가 LIFTED-1의 직접 근거.

### D.5 worklog #125 핵심 인용 (skill audit log — LIFTED-1의 참고 패턴)

> "`skill_library_audits` 테이블 신규 (UUID PK, skill_library_id FK `ondelete=SET NULL`, actor_user_id FK `ondelete=SET NULL`, action String(32), detail JSON, at UtcDateTime). data migration: 기존 skill row에 first admin의 id를 `approved_by`로 채움 + audit 'grandfathered' 엔트리 삽입"

→ LIFTED-1을 mcp_tool_audits로 그대로 복제 가능. grandfather는 불필요(신규 테이블).

### D.6 cycle_guard 현황 인용 (recovery 부재 근거)

cycle_guard.py 80줄 모든 코드 read 결과: `is_cycle_detected → True/False` 반환만. host(decide_policy)가 단순 drop. OpenHands `_handle_loop_recovery_action` 같은 능동 회복 없음 — LIFTED-2의 직접 근거.

### D.7 도입 시 추가 verify 필요한 항목

| 액션 | verify 항목 | 위치 |
|---|---|---|
| A7 Frozen snapshot | 외부 CLI의 prompt cache hit 효과 측정 | `llm_gateway/usage_logger.py` 통계 |
| A9 Cost ceiling | 초기 한도값 산정 — 프로덕션 사용량 분포 | DB usage rows + Prometheus |
| A14 Depth restriction | 현재 handoff가 실제 몇 hop까지 가는지 | worklog #159 + 운영 데이터 |
| A12 동시 write | 룸 shared file 동시 편집 빈도 | worklog 246/255/257 운영 데이터 |
| LIFTED-2 Recovery | cycle_guard 발화 빈도와 falsy positive | structlog + cycle_guard.py 호출자 |

---

*보고서 작성: research/awesome-ai-anatomy-benchmark 브랜치, Claude Opus 4.7. Phase A~E + verify(D.5) 합 7시간 분량 분석.*
