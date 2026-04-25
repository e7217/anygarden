# docs(research): benchmark doorae against awesome-ai-anatomy teardowns

- Commit: `e98f8fb` (e98f8fbb0c4c21855bc76e283287ff074161b8e7)
- Author: Changyong Um
- Date: 2026-04-25T21:14:43+09:00
- PR: —

## Situation

doorae는 11일 신생(첫 커밋 2026-04-14, 88K LoC) 멀티에이전트 채팅 서버이고, 7개 외부 코딩 에이전트 CLI를 호스팅하는 독특한 위치에 있다. 같은 도메인의 다른 OSS 프로젝트들이 어떤 결정을 내렸고 어떤 함정에 빠졌는지를 비교 가능한 형식으로 정리해 둔 자료가 부족했고, 결과적으로 우리가 _이미_ 빠진 함정과 _아직_ 안 빠진 함정을 구분할 lens가 없었다. NeuZhou/awesome-ai-anatomy 레포(16개 AI 에이전트 OSS의 소스 해부 보고서 + META_SCHEMA + CROSS-CUTTING 횡단 분석 + 인터랙티브 비교 도구)가 발견되어, doorae를 그 형식에 자기 매핑하고 가까운 프로젝트들과 정면 비교할 기회가 생겼다.

## Task

- doorae를 awesome-ai-anatomy의 META_SCHEMA에 자기 매핑해 결정 차원의 빈칸을 명시적으로 드러낸다.
- 정면으로 만나는 결정을 가진 프로젝트(oh-my-codex, oh-my-claudecode, OpenHands) 3개를 정밀 정독, doorae LLM gateway/skill library와 직결되는 보조 2개(Cline, Hermes Agent)를 타겟 발췌, CROSS-CUTTING.md 횡단 분석으로 빠진 축을 보강한다.
- 패턴 매칭으로 끝내지 않고 추정에 기반한 항목은 doorae 코드와 직접 대조해 _잠재 버그/현재 위험_을 식별한다.
- 분석 결과를 후속 작업자가 활용 가능한 형식(부록에 원본 추출 노트 보존, 본문에 결론 압축)으로 정리한다.
- main 브랜치를 오염시키지 않도록 worktree에서 진행한다.

## Action

- `.tmp/plan-awesome-ai-anatomy-benchmark.md` 작성 (worktree-plan 스킬 산출물, 4단계 흐름 + 결정 근거 기록).
- worktree `worktrees/research-aaa-benchmark` 생성, 브랜치 `research/awesome-ai-anatomy-benchmark` (main 097d391에서 분기).
- **Phase A** — `docs/research/doorae-meta.yaml` (218줄) 작성. META_SCHEMA의 모든 차원 채움. 핵심 차원(`agent_loop`, `context_management`, `sandbox`, `stuck_detection`)에 소스 위치 주석 첨부. agent_loop이 enum과 안 맞아 `custom`으로 매핑하면서 "engine-loop-as-adapter" 패턴명 명명.
- **Phase B** — 정밀 3개 teardown을 일반 에이전트 3개에 병렬 위임(컨텍스트 보호 목적), 각각 (훔칠 패턴 / 안티패턴 / 결정 분기점) 양식 + 출처 + doorae 적용 라벨로 추출.
- **Phase C** — Cline(providers/hooks/YOLO 영역만) + Hermes(skills/memory 영역만) 보조 에이전트 2개에 병렬 위임.
- **Phase D** — CROSS-CUTTING.md(33KB, 10개 프로젝트 횡단)를 한 에이전트에 위임, 기존 결과를 _보강_하는 임무로 한정. Lens 4·5 + 액션 6개(A9~A14) + 위험 4개(R1~R4) 추가 도출.
- **Phase D.5 (코드 verify)** — 추정에 기반한 항목을 doorae 코드와 직접 대조:
  - `mcp/router.py`, `mcp/tools.py`, `mcp/auth.py`에 `audit` 키워드 0건 확인 → `LIFTED-1` 격상.
  - `cycle_guard.py` 80줄 전체 read, recovery action 부재 확인 → `LIFTED-2` 격상.
  - `find packages -name '*.py' | wc -l` 측정으로 800줄 초과 모듈 9개 확인 → `R1` 위험도 중→고 격상.
  - `mcp/tools.py:_create_skill` → `service.create_from_agent` 경로 확인 → `skill_library_audits` 자동 기록 (R2-a OK).
  - `usage_logger.py` 100줄 read, Anthropic/OpenAI/SSE parser + DB persist 확인 → `A9` 비용 M → S~M 재평가.
  - `safefs.py` 전체 read, O_NOFOLLOW + Limitations docstring 확인 → `worth_stealing` 추가 항목.
  - `skills_library/service.py` 모든 mutation에 `await db.commit()` 일관 → `A12` race 위험 낮음, TIER 6 유지.
- **Phase E** — `docs/research/2026-04-25-awesome-ai-anatomy-benchmark.md` (490줄) 작성. 본문 §1~§7 + 부록 A~D. 부록에 정밀 3 + 보조 2 + 횡단 보강 + verify 상세 원본 노트 보존.

총 산출물: 보고서 1편 (490줄) + META 1편 (218줄) = 708줄, `docs/research/`에 추가.

## Decisions

`.tmp/plan-awesome-ai-anatomy-benchmark.md` §3.2의 결정 + 진행 중 사용자와 합의로 도출:

**비교 깊이 — 5개 균등 정밀 vs 3+2 vs 정밀 3개만 → 3+2 (정밀 3 + 보조 2) 선택**

- (A) 5개 균등 정밀: 일관된 깊이, 직접 비교 표 작성 쉬움. 단 시간 2~3배, Cline의 비핵심 영역(3756줄 God Object 등) 노이즈.
- (B) 3 정밀 + 2 보조: 핵심 결정 차원에 집중, 보조는 타겟 추출 한정.
- (C) 정밀 3개만: 가장 빠름. 단 Cline provider 다중성, Hermes skill 패턴이 doorae에 직결되는데 누락.

결정적 근거: doorae의 핵심 결정(에이전트 격리, 룸 IPC, 컨텍스트, stuck 감지, 권한)이 정밀 3개와 정면으로 맞붙는다. 보조 2개는 doorae가 _이미 굴리고 있는 서브시스템_(provider 다중성, skill library)에 한정한 발췌만 필요하다.

**산출물 형식 — yaml + md 분리 vs 단일 md → 분리 선택**

- (A) `doorae-meta.yaml` + `*.md` 분리: yaml은 awesome-ai-anatomy 스키마와 직접 비교 가능, 미래 자동화 여지.
- (B) 단일 md에 yaml 코드블록 임베드: 한 곳에서 다 봄. 단 스키마와 직접 diff/머지 어려움.

결정적 근거: META_SCHEMA는 awesome-ai-anatomy가 "자동 비교표 생성용"으로 의도한 스키마. 같은 형식을 따르면 후속에 doorae가 그 비교 도구에 정렬되거나, 자체 비교표를 만들 때 입력으로 재사용 가능. 분리 비용은 미미.

**Phase B 실행 — 메인이 직접 정독 vs 병렬 에이전트 위임 → 병렬 에이전트 선택**

각 README가 30KB 내외로, 메인 컨텍스트에 5개를 다 끌어오면 후속 통합 단계 비용 폭발. 각 에이전트에 doorae 핵심 컨텍스트 + 비교 차원 + 추출 양식을 self-contained prompt로 주고 결과만 받기로 결정. 트레이드오프: 에이전트는 doorae 코드를 모르므로 비교 깊이가 약해질 수 있음 → 메인이 통합 단계에서 처리, Phase D.5 verify로 보강.

**Phase D.5 (코드 verify) 신설 — 우선순위 결정 직전 사용자 요청으로 추가**

원안에는 verify가 Phase E의 일부였으나, 사용자가 "코드와 대조해서 확인해봐. 버그 혹은 잠재적 요소를 막는 방향으로"라고 명시 요청해 별도 Phase로 분리. 결과적으로 LIFTED-1/LIFTED-2 발견 + R1 위험도 격상 + A9 비용 재평가 + R3 부분 완화 등 우선순위 재배열의 직접 근거가 됨. **재검토 신호**: 향후 비슷한 분석에서 verify를 Phase E와 분리하는 게 정착시킬 만한 패턴인지.

**보고서 분량 — Full vs Compact vs Compact+부록 → Compact+부록 선택**

본문은 결론 중심(§1~§7, ~3500자), 부록 A~D에 정밀 5개 + 횡단 + verify 원본 노트 보존. 후속 작업자가 본문에서 결론을 잡고 부록에서 원본 추출 노트를 검색할 수 있게 함.

**이슈 등록 — 분석 작업이라 GitHub 이슈 없이 진행**

분석 결과로 도출된 _액션 아이템_은 별건 이슈로 등록하기로 합의. 분석 자체에는 단일 PR 본문에 컨텍스트 명시.

**가정**: awesome-ai-anatomy 저자의 주장은 1차 인용일 뿐 — 도입 결정 시점에 원본 소스 직접 verify 필수. 보고서 §7 "검증 노트"에 명시.

**가정**: CROSS-CUTTING.md가 "16개 프로젝트 횡단"이라는 사용자 추정과 달리 실제로는 10개 프로젝트만 다룸을 Phase D 에이전트가 명시적으로 정정 — 이 사실은 보고서 §7 한계 섹션에 반영.

## Result

- worktree `worktrees/research-aaa-benchmark`, 브랜치 `research/awesome-ai-anatomy-benchmark`에 산출물 2개 파일 (708줄) 추가.
- TIER 1 액션 2개 식별:
  - **LIFTED-1**: MCP tool call audit log 신설 (현재 0건, `skill_library_audits` 패턴 복제, 비용 S~M)
  - **A9**: Cost / Step ceiling layer (인프라 절반 보유, 비용 S~M)
- TIER 2 즉시 가성비 4개 (A3+LIFTED-2 cycle guard 강화 + recovery, A10 order-independent hash, A7 frozen snapshot, R1 PR 정책)
- TIER 3 이하 8개 액션 + 4개 위험 정리, 부록 A~D에 원본 추출 노트 보존.
- 5개 thesis lens 도출:
  - Lens 1 "Deterministic > LLM" 결정 레이어 (doorae 강점)
  - Lens 2 "안전판은 디폴트 ON이거나 의미 없다" (Cline 반면교사)
  - Lens 3 "Host vs Agent loop owner" 정체성 (doorae가 멀티 엔진 평등 호스팅으로 유일)
  - Lens 4 "Loop owner ≠ Cost owner" (doorae A9 신설 근거)
  - Lens 5 "Borrowed core blast radius" (어댑터 단위 7배 위험 — A8 fuzz 확장 근거)
- doorae가 11일 신생임에도 16개 중 다수가 빠진 함정(borrowed core, god-file, security-as-README, env-var-gated permission)을 의식적으로 회피한 신호가 코드에 남아 있음을 객관적으로 확인.
- 후속 작업: TIER 1 액션 2개 + R1 PR 정책 1개를 별건 이슈로 등록 예정 (이 PR 머지 후).
