# LLM 사회자의 Turn-Taking 결함과 그 해결: 멀티에이전트 채팅 시스템 아키텍처 권고

**— 도구 호출 누락·환각·instruction following decay에 대한 2024–2026 연구·프레임워크 분석 및 anygarden 적용 방안**

- Date: 2026-05-12
- Mode: deep-research / deep (8-phase)
- Topic owner: Anygarden 프로젝트 (룸 기반 멀티에이전트 채팅 서버)
- Sources: 25
- Evidence rows: 33
- Triggering observation: V1~V5 PoC 5회 모두 사회자 에이전트가 두 번째 핸드오프부터 mention/handoff 누락하는 결함 재현

---

## Executive Summary

anygarden의 5회차 PoC(V1~V5)에서 관찰된 핵심 결함은 단일 버그가 아니라 LLM의 **세 가지 구조적 약점이 결합한 결과**다. 사회자 에이전트가 첫 핸드오프에서는 `handoff_to(participant_id="...", reason="...")` 또는 `<@user:UUID>` 멘션을 정확히 박지만, 두 번째 턴부터는 일관되게 mention 토큰이나 도구 호출 인자를 누락하고 자연어 텍스트로 핸드오프 *형식*을 흉내낸다. 페르소나 강화 5회(handoff_to 강조 → @멘션 방식 → 모델 명시 → 환각 금지 체크리스트 → 절대 금지 규칙)에도 같은 위치에서 결함이 재현되며 이는 우연이 아닌 LLM의 일관 결함이다.

33개 증거 검토 결과 세 가지 수렴 결과가 드러난다. 첫째, **multi-turn instruction-following decay** — Meta의 Multi-IF [2], MultiChallenge [3] 벤치마크는 *"as the number of turns increases, LLMs increasingly forget to adhere to instructions that were successfully executed in previous turns"* 를 정량화하며, 최상위 Claude 3.5 Sonnet도 멀티턴 instruction 유지에 41.4%만 성공한다. 둘째, **format-task interference** — "Natural Language Tools" 논문 [5]은 도구 호출 schema 강제가 *"some models experiencing more than 20% reductions in accuracy due to task interference"* 를 일으키며, JSON 출력 강제는 GSM8K 정확도를 27.3pp 감소시킨다고 보고한다. 사회자 LLM이 *텍스트로 마커 모양만* 만드는 행동은 이 task interference의 정확한 발현이다. 셋째, **lost-in-the-middle 효과** [4] — 룸 컨텍스트가 커질수록 시스템 프롬프트의 도구 schema는 attention 영역 밖으로 밀려난다.

업계의 처방은 명확하다. AutoGen v0.4의 `SelectorGroupChat` [16]은 `selector_func`을 통해 결정론적 라우터로 LLM 결정을 override할 수 있게 했고, LangGraph는 `Command(goto=...)` [18]와 swarm 패턴 [19]에서 LLM 라우팅의 취약성을 명시적으로 인정한다(*"Start with the supervisor... Graduate to swarm when... your agents rarely misroute"* [20]). MetaGPT [22]와 ChatDev [23]는 LLM 라우팅을 아예 제거하고 type-based publish/subscribe 또는 fixed chat chain으로 대체한다. Anthropic은 자사 Research 시스템에서 멀티에이전트 *오케스트레이션*이 단일 에이전트 대비 90.2% 우위를 보이지만 3–10× 토큰 비용을 치르며, 그마저도 *"the lead agent can't steer subagents, subagents can't coordinate"* 라는 한계를 가진다고 인정한다 [25].

anygarden 팀에 대한 권장은 세 층위로 나뉜다. **단기**(1~2주)로는 `orchestrator` 전략에 *서버측 fallback* 로직을 추가한다 — 사회자 메시지에 mention 토큰이 감지되지 않으면 서버가 즉시 round-robin 다음 멤버를 자동 nominate 한다. **중기**(1~3개월)로는 Anthropic `tool_choice: {"type": "any"}` [9] 또는 OpenAI structured outputs [8]를 활용한 **constrained handoff** 통로를 어댑터별로 추가해, 사회자가 도구 호출을 강제당하도록 한다. **장기**(3~12개월)로는 Magentic-One의 dual-loop 패턴 [28]을 모사한 **stall-detection + replan** 메커니즘과, MetaGPT식 type-subscribed 라우팅으로 LLM 사회자 의존도 자체를 낮춘다. 모든 권장의 공통 원칙: *LLM이 turn-taking을 결정하지 않고, 도구 호출 인자/제약을 통해 forced compliance 시킨다.*

---

## 1. Introduction

### 1.1 연구 질문

멀티에이전트 LLM 채팅 시스템에서 **사회자(facilitator/orchestrator) 역할 에이전트가 다음 발언자 지명에서 일관되게 실패하는 패턴**은 어떻게 정의되며, 학계·산업계가 그 결함을 어떤 메커니즘으로 방어하는가? 그 처방은 anygarden의 룸 기반 아키텍처(`packages/agent/anygarden_agent/integrations` + `packages/cluster/anygarden`)에 어떻게 구체화되는가?

### 1.2 결함 관찰 (anygarden V1~V5 PoC 요약)

| PoC | 사회자 페르소나 | 첫 mention/handoff | 두 번째 | 비고 |
|---|---|---|---|---|
| V1 | `handoff_to` 도구 사용 지시 | ✓ 정확 | ✗ mention 토큰 누락 | task description은 잘 작성 |
| V2 | + 인자 누락 방지 강조 | ✓ | ✗ 동일 패턴 | 정확히 같은 위치 |
| V3 | `@멘션` 텍스트 방식으로 전환 | ✓ | ✗ 자연어 안내만 | 도구 호출 회피 |
| V4 | + 모델 명시(`gpt-5.4-mini`) | ✓ | ✗ + 환각 시작 | Critic 미발언인데 "지적했다"고 합성 |
| V5 | + 환각 금지·체크리스트·절대 금지 규칙 | ✓ | ✗ 동일 패턴 | 5번째도 같은 결함 |

코드 위치: `packages/agent/anygarden_agent/integrations/claude_code.py:374-424`(handoff_to MCP 툴), `packages/cluster/anygarden/ws/handler.py:154-221`(서버 측 stamping), `packages/agent/anygarden_agent/integrations/base.py:594-599`(decide_policy의 next_speaker 검사).

### 1.3 범위와 방법

검토 범위는 2024–2026년의 LLM tool-use 신뢰도 연구, 멀티에이전트 프레임워크 (AutoGen, LangGraph, CAMEL, AgentVerse, MetaGPT, ChatDev, Magentic-One, OpenAI Swarm), Anthropic·OpenAI 공식 문서, arXiv 논문, 그리고 production 라이브러리(Instructor, Guardrails AI)를 포함한다. 2023년 이전 고전 분산 시스템 패턴(actor model, publish/subscribe)은 개념적 뿌리만 참조한다.

방법론은 deep-research 8단계 파이프라인에 따랐으며, 3개 병렬 sub-agent를 사용해 (a) framework turn-taking 메커니즘, (b) LLM tool-use 신뢰도·instruction decay, (c) speaker selection 알고리즘·supervisor 패턴을 각각 조사했다. 총 33개 evidence를 수집하고 5개 finding으로 압축했으며, 핵심 주장마다 3개 이상의 독립 소스로 교차 검증했다.

### 1.4 가정

독자는 anygarden 팀의 기술 독자이며 LLM MAS 개념과 anygarden의 룸/참여자/엔진 어댑터 아키텍처에 익숙하다. "Turn-taking", "speaker selection", "handoff"는 동의어로 사용한다. "사회자(moderator)", "오케스트레이터(orchestrator)", "supervisor"는 발언권 분배 역할을 하는 에이전트를 가리키는 동의어로 사용한다.

---

## 2. Main Analysis

### 2.1 Finding 1 — 사회자 결함은 단일 버그가 아니라 LLM의 세 가지 구조적 약점이 결합한 결과

anygarden의 V1~V5에서 관찰된 패턴은 학계가 이미 정량화한 세 가지 LLM 결함이 동시에 발현된 것이다.

첫째, **multi-turn instruction-following decay**. Laban et al. [1]는 200,000개의 시뮬레이션 대화에서 *"all top open- and closed-weight LLMs exhibit significantly lower performance in multi-turn conversations than single-turn, with an average drop of 39%"* 를 보고하며, 그 39%를 *"a minor loss in aptitude and a significant increase in unreliability"* 로 분해한다. 즉 LLM이 능력을 잃은 게 아니라 *분산이 커진다.* Meta의 Multi-IF 벤치마크 [2]는 *"as the number of turns increases, LLMs increasingly forget to adhere to instructions that were successfully executed in previous turns"* 를 직접 측정하며, MultiChallenge [3]은 *"all frontier models have less than 50% accuracy on MultiChallenge, with the top-performing Claude 3.5 Sonnet achieving just a 41.4% average accuracy"* 를 보고한다. anygarden 페르소나 5회 강화의 결과가 *모두 같은 위치에서 실패*한 것은 우연이 아니라 이 통계적 규칙성의 정확한 표현이다.

둘째, **format-task interference**. "Natural Language Tools" 논문 [5]은 결정적 통찰을 제공한다 — *"Structured formats require models to simultaneously handle multiple competing demands such as understanding the query, selecting appropriate tools, adhering to format constraints, and generating a response, with some models experiencing more than 20% reductions in accuracy due to task interference. Additionally, requiring JSON output reduced response accuracy by 27.3 percentage points on the GSM8K benchmark compared to natural language."* anygarden 사회자가 첫 핸드오프에서는 `handoff_to(...)` 도구를 정확히 호출하지만 두 번째부터는 `[HANDOFF] ...` 텍스트만 작성하는 행동은 이 task interference의 직접적 발현이다. 컨텍스트가 길어질수록 schema 준수 부담이 자연어 생성 우선순위에 밀려난다.

셋째, **lost-in-the-middle 효과**. Liu et al. [4] (TACL 2024)는 *"Performance is often highest when relevant information occurs at the beginning or end of the input context, and significantly degrades when models must access relevant information in the middle of long contexts."* Du et al. 후속 연구는 *"context length alone degrades performance, independent of retrieval quality. Even when irrelevant tokens are replaced with whitespace, performance still drops 13.9% to 85% as input length increases."* 사회자의 도구 schema는 시스템 프롬프트 위치 0에 있고, 라운드 2 시점이면 룸 발화가 누적되어 schema는 attention 영역 밖으로 밀려난다.

추가로 **goal drift** [6]가 이 세 가지를 가속한다 — *"all evaluated agents exhibit patterns of goal drift upon encountering competing objectives"*. 룸 참여자들의 발화 각각이 사회자에게는 새로운 micro-objective로 작용해 원래의 "매 메시지에 mention 박기" 목표를 점진적으로 침식한다. V4에서 발견된 *환각* — Critic이 발언하지도 않았는데 "Critic이 ROI 어려움을 지적했다"고 합성한 사건 — 은 instruction decay + format interference + goal drift의 최종 발현이다.

증거 수렴: 세 가지 결함은 서로 독립적으로 측정·검증된 현상이며, anygarden가 관찰한 패턴은 이들의 *예측 가능한 결합*이다. 따라서 페르소나 프롬프트 강화로 풀리지 않는다 — 페르소나는 *in-band* 처방이고, 결함의 원인이 in-band 신호의 약화 자체이기 때문이다.

### 2.2 Finding 2 — 5개 메이저 프레임워크가 모두 결정론적 라우터 + LLM 하이브리드로 수렴

2024–2026년 멀티에이전트 프레임워크 진화의 공통 방향은 **순수 LLM 위임 거부**다. 다섯 가지 사례 모두 같은 결론에 도달한다.

**AutoGen**. v0.2 GroupChat은 `speaker_selection_method ∈ {"auto", "round_robin", "random", "manual"}` 네 가지 옵션을 제공했고 [11], `"auto"` 모드는 별도 LLM 호출로 다음 발언자를 결정한다. 그러나 GitHub Issue #842는 anygarden의 결함과 정확히 동일한 실패 모드를 문서화한다 — *"GroupChat select_speaker failed to resolve the next speaker's name. This is because the speaker selection OAI call returned: ... The speaker selection mechanism returns a role name with underscores while the actual registered role uses spaces"* [12]. 2024년 2월 FSM GroupChat 블로그 [13]는 `allowed_or_disallowed_speaker_transitions` 인자로 FSM 스타일 전환 규칙을 추가했고, 2025년 1월 v0.4 redesign에서 `SelectorGroupChat`이 출시되며 *"The selector_func parameter allows custom selection logic to bypass the default model-based mechanism. Returning None from the custom selector function will use the default model-based selection"* [16] — 결정론적 라우터가 first-class citizen이 되고 LLM은 fallback으로 강등됐다. 그럼에도 Issue #4289 [17]는 *"SelectorGroupChat ignores selector function randomly"* 를 보고하며 hybrid layer조차 추가 방어가 필요함을 보인다.

**LangGraph**. 2024년 12월 10일 `Command(goto=...)` 도입 블로그 [18]는 명시적으로 *"requiring edges to connect nodes can sometimes make it harder or unintuitive to express more dynamic logic"* 라고 한정된 LLM-routing의 약점을 인정한다. langgraph-swarm [19]은 *"The system remembers which agent was last active, ensuring that on subsequent interactions, the conversation resumes with that agent. Custom handoff tools update the swarm state by including 'active_agent': agent_name in the Command update."* 즉 active speaker는 *상태 필드*로 영속되며 LLM이 잊어도 시스템이 기억한다. LangChain 공식 가이드 [20]는 결정적으로 — *"Start with the supervisor as it's simpler to build and debug. Graduate to swarm when you have data showing latency is the bottleneck and your agents rarely misroute"* — LLM 라우팅 실패가 일상적임을 전제로 디자인 가이드를 작성한다.

**CAMEL** [21]은 가장 단순한 해법을 택했다: 두 에이전트(AI User + AI Assistant)가 *strictly alternate*하며 종료는 하드코딩 sentinel `<CAMEL_TASK_DONE>`로 결정. speaker selection 문제 자체가 존재하지 않는다 — turn order가 구조적으로 고정.

**AgentVerse** [22] (ICLR 2024)는 4단계 결정론적 흐름(Expert Recruitment → Decision-Making → Action Execution → Evaluation) 위에서만 LLM이 *stage 안에서* 작동한다. *오케스트레이션 boundary*는 LLM이 결정하지 않는다.

**MetaGPT** [23]는 LLM-mediated routing을 *완전히 제거*하고 publish/subscribe로 대체한다 — *"MetaGPT employs structured communication interfaces and a publish-subscribe mechanism for efficient information sharing. ... agents publish messages with specific cause_by attributes, allowing other agents to subscribe to these message types. The inputs of an agent determine the value of the rc.watch attribute ... All handovers between agents must comply with certain established standards that reduce the risk of hallucination caused by idle chatter between LLMs."* 라우팅이 type-based subscription으로 결정되므로 LLM 사회자가 필요 없다. **ChatDev** [24]는 waterfall phase 안에 fixed chat chain (CEO/CPO, CTO/programmer 등)을 박아 *"communicative dehallucination — agents request more detailed information before responding directly"* 라는 방어층을 추가한다.

**OpenAI Swarm** [33]은 가장 미니멀한 형식화 — *"An agent is a system prompt plus a list of functions. A handoff is a function that returns a different agent. That is the entire API surface."* Handoff이 *typed Python function*이므로 LLM이 자연어로 흉내낼 여지가 구조적으로 없다.

수렴: **2024–2026년 mature framework는 모두** (a) deterministic dispatcher를 기본으로 두고, (b) LLM을 *transition boundary*에서만 호출하며, (c) LLM이 실패해도 시스템이 진행하는 fallback contract를 갖는다. anygarden의 `orchestrator` 전략은 LLM에 turn-taking을 완전히 위임하는데, 이는 2025년 시점 업계 표준과 정면 충돌한다.

### 2.3 Finding 3 — 도구 호출 신뢰도는 in-band 강화로 풀리지 않고 decoder-level 제약이 유일한 견고한 해법

anygarden가 페르소나 5회 강화로 풀지 못한 결함의 정확한 해법은 학계가 이미 정립했다 — **constrained decoding at the sampler level**.

**OpenAI Structured Outputs** (2024년 8월) [8]는 grammar-based constrained decoding을 적용해 *"On evals of complex JSON schema following, the new model gpt-4o-2024-08-06 with Structured Outputs scores a perfect 100% and achieves 100% reliability in evals, perfectly matching the output schemas. ... constrained decoding is a technique that manipulates a generative model's token generation process to constrain its next-token predictions to only tokens that do not violate the required output structure."* 결정적인 부분: 제약이 *prompt level*이 아니라 *token sampler level*에 박힌다. LLM이 schema를 *잊으려 해도* token sampler가 schema 외 token을 생성 확률 0으로 만든다.

**Anthropic Claude**의 `tool_choice` [9]는 더 직접적이다 — *"When you have tool_choice set to any or a specific tool, the API prefills the assistant message to force a tool to be used. This means that the models will not emit a natural language response or explanation before tool_use content blocks, even if explicitly asked to do so."* 사회자 LLM은 도구 호출 *없이는 메시지를 만들 수 없도록* 물리적으로 제약된다. anygarden의 V1~V5에서 사회자가 두 번째부터 `[HANDOFF]` 텍스트만 만든 결함은 이 제약 한 줄로 사라진다.

**BFCL V4** [10] (Berkeley Function Calling Leaderboard)는 production 신뢰도 측정의 표준이며, *"hallucination detection identifies whether the values of input parameters in function calls are fabricated — not mentioned in either the user query or the system prompt. ... While the top-performing models excel in single-turn, crowd-sourced, and hallucination-related metrics, there remains significant room for improvement in multi-turn scenarios."* anygarden의 결함은 정확히 multi-turn 시나리오의 parameter hallucination/omission이며, 모든 frontier 모델이 single-turn에서는 잘하지만 multi-turn에서는 *유의미한 결함이 남아있음*을 BFCL이 정량화한다. 즉 모델 업그레이드만으로는 해결되지 않는다.

**Validator + retry 패턴**. Instructor 라이브러리 [10b]는 production에서 검증된 fallback 구조를 제공한다 — *"If the LLM produces invalid output, Instructor retries with the validation error in the prompt, giving the model specific feedback about what went wrong."* Guardrails AI도 *"validates output against schemas and re-prompts automatically when the structure is wrong"*. 핵심은 *검증 결과를 다음 prompt에 inline*하는 것 — LLM이 자기 실수를 직접 본다.

처방의 위계는 명확하다:
1. **API-level constrained decoding** (Anthropic `tool_choice: any`, OpenAI Structured Outputs) — *가장 강력*, 결함 클래스 자체를 제거
2. **Validator + retry** — API constraint 적용 못 하는 환경에서 fallback
3. **Decoder context 압축** — 핸드오프 결정 직전 context를 truncate해 schema를 high-attention 위치로 복귀
4. **In-band 페르소나 강화** — 효과 거의 없음 (이미 5회 검증)

LLM-based 에이전트 hallucination 서베이 [25]는 같은 결론을 일반화한다 — *"LLMs frequently experience breakdowns in execution stability, including malformed tool calls, loss of structure in JSON output, or forgetting earlier decisions."*

### 2.4 Finding 4 — Supervisor 패턴은 LLM이 stall할 때 deterministic fallback이 필수다

LLM 사회자가 *완전히 멈출 때*의 처방을 학계와 업계 모두 같은 방향으로 정형화했다 — **stall detection + deterministic fallback contract**.

**Magentic-One** (Microsoft, 2024년 11월) [27, 28]은 dual-loop Orchestrator를 정의한다. *"At each step of its plan, the Orchestrator creates a Progress Ledger where it self-reflects on task progress and checks whether the task is completed. ... If the Orchestrator finds that progress is not being made for enough steps, it can update the Task Ledger and create a new plan."* 즉 사회자가 막히면 메타 수준에서 plan을 재구성한다. anygarden의 V4 환각 사건(Critic이 발언 없이 종료 강행)은 정확히 이 stall 감지 없이 사회자가 *자기 머리로 진행을 합성*한 경우다.

**AutoGen v0.2**는 같은 원칙을 더 단순하게 구현한다 [11] — *"If we run out of turns and no single agent can be determined, the next speaker in the list of agents is returned (after exhausting max_retries_for_selecting_speaker, default: 2)."* LLM 셀렉터가 N회 retry 후에도 결정 못 하면 *결정론적으로 다음 에이전트로 넘어간다*. 즉 round-robin이 LLM auto-selection의 final fallback이다.

**Anthropic 멀티에이전트 시스템** [26]은 한계까지 인정한다 — *"the lead agent can't steer subagents, subagents can't coordinate"*. orchestrator-worker 구조가 단일 에이전트 대비 90.2% 우위를 보이지만 *"multi-agent implementations typically use 3-10x more tokens than single-agent approaches"* [29]. Anthropic은 *"Early agents made errors like spawning 50 subagents for simple queries, scouring the web endlessly for nonexistent sources, and distracting each other with excessive updates"* 같은 pathology를 일찍 마주쳤고, 그래서 *"Start with the simplest approach that works, and add complexity only when evidence supports it"* 를 공식 가이드로 등재한다 [29]. anygarden 팀이 "orchestrator 전략을 기본으로 두고 mention 못 박으면 멈춤"이라는 결정을 내린 것은 이 가이드에 어긋난다.

**MAST (Multi-Agent System Failure Taxonomy)** (Cemri et al., NeurIPS 2025) [30]는 1600+ traces를 7개 프레임워크에서 분석해 14개 failure mode를 3개 카테고리로 분류했다 (κ=0.88 inter-rater agreement). anygarden가 마주한 결함의 정확한 분류:
- **FM-1.2: Disobey role specification** (사회자가 mention 박지 않음)
- **FM-2.3: Task derailment** (라운드 강제 종료)
- **FM-2.10: Ignored other agent's input** (Critic 미발언 무시)
- **FM-3.1: Premature termination** (라운드 1 종료 환각)

anygarden V4는 네 가지를 *동시에* 발현시켰다. 이는 단순 버그가 아니라 *system design 문제* (카테고리 1)임을 시사한다 — 추가적 LLM 정렬이 아니라 *상위 시스템*이 LLM 결정을 검증·교정하는 layer가 필요하다.

**LangGraph supervisor vs swarm** [20]은 같은 통찰을 실용 가이드로 정리한다. Supervisor (중앙 LLM이 매번 다음 에이전트 결정)는 디버깅이 쉽지만 매 turn마다 LLM call이 필요. Swarm (handoff tool로 에이전트가 직접 다음 에이전트 호출)은 latency가 낮지만 *agents rarely misroute*라는 전제가 충족돼야 한다. anygarden는 swarm 전제가 *깨졌음을 5회 PoC로 입증*했다.

수렴: 모든 supervisor 기반 시스템은 (a) progress monitoring, (b) deterministic fallback (round-robin/next-in-list), (c) human escalation의 세 가지 contract를 갖춰야 한다. anygarden는 셋 다 부재하다.

### 2.5 Finding 5 — Addressee Recognition은 LLM의 weak link이며 서버측 명시 라우팅이 우위

anygarden가 다음 단계 설계 시 *"사회자 LLM에게 addressee 결정을 더 위임하면 풀린다"* 는 유혹을 받기 쉽다. 그러나 2025년 학계 데이터는 이 방향이 회귀임을 보여준다.

**Addressee Benchmark** (IWSDS 2025) [31]는 triadic dialogue에서의 GPT-4o 성능을 측정했고 결과는 충격적이다 — *"GPT-4o achieved an accuracy of 80.9%, which is only marginally above chance level (80.1%). The model tends to output 'O', indicating that it often fails to recognize when an utterance is directed at a specific participant."* 즉 frontier 모델조차 3자 대화에서 "누구에게 말하는 것인가"를 *chance 수준*으로만 인식한다. 더 충격: gaze (시선) feature를 추가하면 정확도가 *75.2%로 떨어진다* — 추가 신호가 도움이 안 된다.

**SI-RNN** (AAAI 2018) [32]은 *jointly* addressee + response를 예측하는 role-sensitive RNN을 제안했고, *"unlike previous work that selected the addressee and response separately, SI-RNN selects them jointly by viewing the task as a sequence prediction problem"* 으로 separate 예측 baseline을 outperform 했다. 7년 전 전용 모델이 LLM zero-shot보다 정확하다 — addressee recognition이 LLM의 일반화가 부족한 영역임을 보인다.

**"Lazy agent"** 현상 [10c] (OpenReview 2025)은 또 다른 차원의 위험을 보여준다 — *"lazy agent behavior, in which one agent dominates while the other contributes little, undermining collaboration and collapsing the setup to an ineffective single agent."* anygarden의 PoC에서도 사회자가 같은 에이전트(주로 Visionary)에게 반복 핸드오프하고 Critic을 자주 빠뜨린 패턴이 보였다. round-robin 같은 결정론적 분배가 이를 원천 방지한다.

anygarden는 이미 강점을 갖고 있다 — `packages/cluster/anygarden/orchestration/rules.py`의 `parse_mentions()` 가 `<@user:id>`, `<#room:id>`, `@Name` 세 형태를 *서버에서* 파싱해 `metadata.mentions`로 명시 첨부한다. 이는 LLM의 addressee 추론보다 *구조적으로 정확도가 높고*, audit 가능하며, 멀티엔진 환경에서 엔진별 LLM 성능 편차에 영향받지 않는다.

그러나 강점이 약점을 가린다 — 사회자 *발신* 메시지는 *사회자가 mention 토큰을 박을 때만* 라우팅된다. 사회자 LLM이 박지 않으면 mention 파싱은 trigger 되지 않고, decide_policy는 "내 차례 아님" 으로 SKIP한다. anygarden의 V1~V5 결함은 정확히 *이 강점에 의존한 약점*이다.

처방은 분명하다 — addressee recognition을 LLM에 위임하지 말 것. 명시적 mention 부재 시 *서버측 fallback*(round-robin 다음 발화자 자동 nominate)이 LLM의 *복원 시도*보다 압도적으로 견고하다. 동시에 UI 측 mention 자동완성·제안을 강화해 사용자도 사회자도 mention을 박기 쉽게 만든다.

---

## 3. Synthesis & Insights

세 축의 증거가 하나의 권장 아키텍처로 수렴한다.

**축 1 — LLM에 turn-taking을 *완전히* 위임하지 않는다.** Finding 1(decay)과 Finding 3(format interference)이 LLM의 한계를 정량화하며, Finding 5(addressee recognition)가 자연어 추론의 약점을 보인다. 페르소나 강화는 *in-band* 처방이고 *in-band* 약화가 원인이므로 처방이 효과 없다.

**축 2 — 결정론적 dispatcher가 기본, LLM은 transition boundary에서만 보조.** Finding 2의 5개 프레임워크가 모두 이 방향으로 수렴했다. AutoGen `selector_func`, LangGraph `Command(goto=...)`, MetaGPT publish/subscribe, OpenAI Swarm handoff function — 모두 deterministic primitive를 도입하고 LLM을 *결정 함수*로 좁힌다.

**축 3 — LLM이 결정하더라도 *forced compliance*로 좁힌다.** Finding 3의 constrained decoding(Anthropic `tool_choice: any`, OpenAI Structured Outputs)이 가장 견고하다. Finding 4의 stall-detection + deterministic fallback(Magentic-One, AutoGen `max_retries_for_selecting_speaker`)이 *constrained decoding 적용 불가*한 엔진(예: anygarden의 Claude Code SDK, codex CLI, gemini-cli)을 위한 두 번째 방어선이다.

이 세 축을 anygarden에 겹치면 구조가 명확해진다:

1. **anygarden는 이미 결정론적 dispatcher 인프라를 보유**: `round_robin` 전략 (`packages/cluster/anygarden/ws/handler.py:225` `_compute_round_robin_next`), `next_speaker_participant_id` stamping (handler.py:154-221), `MessagePolicy.{RESPOND, INGEST_ONLY, SKIP}` (base.py:83), `decide_policy` (base.py:392). 사용 활성화만 하면 Finding 2 권고에 즉시 부합한다.
2. **`orchestrator` 전략의 결함**: 사회자 LLM에 turn-taking을 *완전히* 위임하고 fallback 없음. Finding 4의 deterministic fallback contract가 부재.
3. **`handoff_to` MCP 툴**: 구현은 정상 (`packages/agent/anygarden_agent/integrations/claude_code.py:374-424`)이지만 LLM이 *호출하지 않을 때* 서버측 보호가 없음. Finding 3의 constrained decoding 적용 가능 여부가 어댑터마다 다름.

권장 아키텍처는 *세 층위의 hybrid*:

- **Layer 1 (기본)**: `mentioned_only` + UI mention 자동완성. 사용자/에이전트가 명시 mention 박으면 작동. 가장 단순·견고.
- **Layer 2 (서버 fallback)**: `orchestrator` 전략에 *mention 없는 메시지 감지 시 round-robin 자동 nominate* 추가. anygarden handler.py에 ~20줄 추가로 구현 가능.
- **Layer 3 (어댑터별 forced compliance)**: claude-code 어댑터는 Anthropic SDK의 `tool_choice` API를 활용해 *특정 메시지에서 `handoff_to` 도구 호출 강제*. codex/gemini는 prompt-level + validator+retry로 우회.

이는 학계 합의(Finding 2)와 production 가드(Finding 4)에 정렬되며 anygarden의 기존 강점(서버측 mention 파싱, multi-engine 추상화)을 보존한다.

마지막 통찰 — **PoC V1~V5의 5회 실패는 실험 실패가 아니라 *진단 성공*이다.** LLM 페르소나 강화가 효과 없음을 5회로 입증했고, 학계 정량 데이터(Multi-IF 39%, MultiChallenge 41.4%, Natural Language Tools 27pp drop, Addressee 80.1% chance)와 정확히 일치한다. 다음 단계는 LLM 강화가 아니라 *시스템 layer 추가*다.

---

## 4. Limitations & Caveats

본 연구는 25개 소스, 33개 evidence row에 기반하며 다음 공백을 인정한다.

첫째, anygarden 결함은 5회 PoC로 *반복 관찰*되었으나 *N=5의 행동 관찰*이지 *통계적 유의성 검증*이 아니다. 다른 시스템 프롬프트·다른 컨텍스트 길이·다른 모델에서 빈도가 어떻게 달라지는지는 추가 실험이 필요하다. Multi-IF/MultiChallenge 같은 표준 벤치마크와의 정량 비교는 본 연구 범위 밖이다.

둘째, Anthropic `tool_choice: {"type": "any"}` [9]가 Claude Code SDK의 `query()` 경로에서 *실제 어떻게 노출되는지*는 본 연구에서 확인하지 못했다. SDK가 raw API의 `tool_choice` 인자를 그대로 노출하지 않을 가능성이 있으며, 그 경우 단기 처방의 Layer 3은 코드 수정이 더 무거워진다. 프로토타이핑 단계에서 검증 필요.

셋째, codex CLI와 gemini-cli의 tool-use 신뢰도는 BFCL V4 [10]가 OpenAI/Anthropic/Google 모델별로 측정한 값이지 *anygarden adapter 경로*에서의 신뢰도가 아니다. 어댑터별로 prompt-level 처방의 효과 편차가 클 수 있다.

넷째, MAST 14 failure mode [30]는 7개 프레임워크 1600+ traces 기반이지만 anygarden는 이 데이터셋에 포함되지 않았다. anygarden의 *분포*가 평균과 다를 수 있다.

다섯째, 본 연구는 사회자 LLM의 turn-taking 결함이라는 *단일 축*에 집중했다. 인접 문제 — 멀티엔진 컨텍스트 동기화, 토큰 비용 최적화, 사용자 경험 — 는 가볍게 다루었다. 특히 round_robin 전환이 사회자의 *의도적 발언권 조정*(예: "토론이 한쪽으로 쏠리면 반대 입장에게 발언권" 같은 동적 판단)을 포기하는 trade-off는 본 연구가 충분히 다루지 않았다.

여섯째, sub-agent 3개로 병렬 조사했고 각자 다른 검색 각도를 사용했으므로 *중복 발견*은 적지만 *놓친 영역*은 있을 수 있다. 특히 2026년 1~5월 최신 논문 동향은 sub-agent 검색 cutoff에 따라 일부 누락 가능.

마지막으로, 이전 연구 [anygarden 2026-04-19 multi-agent context injection] 가 다룬 `ingest_context` 결함과 본 연구의 turn-taking 결함은 *별개 issue*이지만 둘 다 사회자/멀티에이전트 협업의 견고성에 영향한다. 두 처방이 충돌하지 않는지(특히 `context_window_enabled=true` 상태에서 round_robin이 자연스럽게 작동하는지)는 통합 검증이 필요하다.

---

## 5. Recommendations

### 단기 (1~2주) — 서버측 fallback 추가

**핵심 변경**: `packages/cluster/anygarden/ws/handler.py`에 *mention 누락 자동 감지 + round-robin nominate* 로직 추가.

알고리즘 의사코드:
```
on message_received(msg):
    if room.speaker_strategy == "orchestrator" and msg.sender_kind == "agent":
        if msg.metadata.mentions is empty AND content is not [종료] marker:
            # 사회자가 mention 박지 않음 — round-robin fallback
            next_pid = compute_round_robin_next(
                room, current_speaker=msg.sender_pid
            )
            msg.metadata["next_speaker_participant_id"] = next_pid
            log.warning("orchestrator_mention_fallback", room=room.id, ...)
```

이는 Finding 4의 AutoGen `max_retries_for_selecting_speaker` [11] 후 round-robin 패턴을 anygarden에 적용한 것. 코드 위치: `_is_ambient_candidate` 다음 (handler.py:967 근처). `_compute_round_robin_next`는 이미 존재 (handler.py:225). 추가 20~30줄로 구현 가능.

기대 효과: V1~V5 같은 결함이 발생해도 토론이 *멈추지 않고* 자동으로 다음 발언자로 넘어감. 사회자 LLM이 *복구할 기회*를 얻거나 round-robin이 *완전히 대체*한다.

병행: `room.speaker_strategy="round_robin"` PoC 1회 시도 — 사회자 *제거*하고 3명 참여자가 서버 자동 회전으로 라운드 끝까지 가는지 검증 (목표: V6 토론 룸, 라운드 3 완주 + 종료 합의 도출).

### 중기 (1~3개월) — Constrained handoff + validator+retry

**Phase A — Anthropic `tool_choice` 활용 (claude-code 어댑터)**: claude-code adapter에서 *특정 룸 상태*(orchestrator 전략 + 사회자 차례)일 때 `query()`에 `tool_choice={"type": "tool", "name": "handoff_to"}` 또는 `{"type": "any"}` [9]를 박는다. Claude Agent SDK가 이를 expose하는지 검증 후 구현. 결과: 사회자 LLM이 `handoff_to` 도구 호출 *없이는* 메시지를 만들 수 없다 — 결함 원천 제거.

**Phase B — Validator+retry (codex/gemini-cli/openhands)**: tool_choice 미지원 엔진은 다음 패턴 적용 — 사회자 메시지 발신 직전 mention 토큰 또는 도구 호출 존재 검증, 실패 시 *재시도 prompt* 자동 발행 (검증 실패 사유 inline). Instructor 라이브러리 [10b] 패턴 모사. 구현 위치: 각 어댑터의 `on_message`/`_collect_reply` 사이.

**Phase C — Stall-detection + escalation**: Magentic-One Progress Ledger [27, 28] 단순화 모사. 사회자가 동일 사이클(자기→자기 → 자기 응답) N회 반복하거나 환각 패턴(다른 멤버 발화 없이 진행) 감지 시 *알람 + human 호출*. anygarden의 `cycle detection` 인프라(`is_cycle_detected`, base.py:543) 확장.

### 장기 (3~12개월) — 아키텍처 전환

**옵션 A — Publish/subscribe routing 도입**: MetaGPT [23]식 type-based subscription을 anygarden 룸 모델에 추가. 룸별 워크플로우 SOP를 선언해 `Researcher.cause_by=DELEGATED_RESEARCH → Writer.watch=RESEARCH_DONE` 같은 정형 규칙. orchestrator 전략의 *대안*이지 대체 아님.

**옵션 B — Magentic-One 스타일 dual-loop orchestrator**: Task Ledger(룸 골 + facts) + Progress Ledger(현재 라운드 상태). 사회자 LLM은 *plan 단위*에서만 호출되고 turn-taking은 ledger update가 자동 결정. anygarden의 Goal 시스템(`packages/cluster/anygarden/goals/`)을 확장해 구현 가능.

**옵션 C — 멀티엔진 정렬 정책**: BFCL V4 [10] 같은 표준 벤치마크를 anygarden 어댑터별 응답 신뢰도 측정에 활용. claude-code/codex/gemini-cli/openhands가 각각 어떤 시나리오에서 더 신뢰할 수 있는지 정량화하고, 룸 설정 시 사회자 엔진 자동 추천.

### 안티-권장 (절대 하지 말 것)

- **페르소나 추가 강화로 turn-taking 풀려고 시도** — 본 연구가 5회 데이터로 무효함을 입증. Finding 1의 instruction decay가 페르소나 강화의 메커니즘 자체를 약화시킨다.
- **LLM에 addressee recognition 위임** — Finding 5의 GPT-4o 80.9% (chance 80.1%) 데이터 참조. 자연어 추론은 서버 mention 파싱보다 *구조적으로 열위*.
- **`orchestrator` 전략을 새 룸의 기본값으로 설정** — 5회 PoC가 보인 실패율 + 학계 데이터 + Anthropic 가이드 [29] 모두 권장하지 않음. `mentioned_only`(가장 단순) 또는 `round_robin`(가장 견고)을 기본으로.

---

## 6. Bibliography

[1] Laban, P. et al. (2025). "LLMs Get Lost In Multi-Turn Conversation". arXiv:2505.06120. https://arxiv.org/pdf/2505.06120 (Retrieved 2026-05-12)

[2] He, Y. et al. (2024). "Multi-IF: Benchmarking LLMs on Multi-Turn and Multilingual Instructions Following". Meta. arXiv:2410.15553. https://arxiv.org/html/2410.15553v2

[3] Sirdeshmukh, A. et al. (2025). "MultiChallenge: A Realistic Multi-Turn Conversation Evaluation Benchmark". arXiv:2501.17399. https://arxiv.org/abs/2501.17399

[4] Liu, N. F. et al. (2024). "Lost in the Middle: How Language Models Use Long Contexts". TACL. https://aclanthology.org/2024.tacl-1.9/

[5] (2025). "Natural Language Tools: A Natural Language Approach to Tool Calling In Large Language Agents". arXiv:2510.14453. https://arxiv.org/html/2510.14453v1

[6] (2025). "Technical Report: Evaluating Goal Drift in Language Model Agents". arXiv:2505.02709. https://arxiv.org/html/2505.02709v1

[7] (2025). "AgentIF: Benchmarking Instruction Following in Agentic Scenarios". arXiv:2505.16944. https://arxiv.org/html/2505.16944v1

[8] OpenAI (2024). "Introducing Structured Outputs in the API". https://openai.com/index/introducing-structured-outputs-in-the-api/

[9] Anthropic (2025). "How to implement tool use — Claude API Docs". https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use

[10] UC Berkeley (2025). "Berkeley Function Calling Leaderboard (BFCL) V4". https://gorilla.cs.berkeley.edu/leaderboard.html

[10b] Instructor (2025). "Understanding Semantic Validation with Structured Outputs". https://python.useinstructor.com/blog/2025/05/20/understanding-semantic-validation-with-structured-outputs/

[10c] (2025). "Unlocking the Power of Multi-Agent LLM for Reasoning: From Lazy Agents to Deliberation". OpenReview. https://openreview.net/forum?id=5J6u03ObRZ

[11] Microsoft (2024). "agentchat.groupchat — AutoGen 0.2 reference". https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/groupchat/

[12] Microsoft (2024). "GitHub Issue #842 — GroupChat select_speaker failed to resolve the next speaker's name". https://github.com/microsoft/autogen/issues/842

[13] Microsoft (2024). "FSM Group Chat — User-specified agent transitions". https://microsoft.github.io/autogen/0.2/blog/2024/02/11/FSM-GroupChat/

[14] Microsoft (2024). "Customize Speaker Selection — AutoGen 0.2". https://microsoft.github.io/autogen/0.2/docs/topics/groupchat/customized_speaker_selection/

[15] Microsoft Research (2025). "AutoGen v0.4: Reimagining the foundation of agentic AI". https://www.microsoft.com/en-us/research/articles/autogen-v0-4-reimagining-the-foundation-of-agentic-ai-for-scale-extensibility-and-robustness/

[16] Microsoft (2025). "Selector Group Chat — AutoGen v0.4 stable docs". https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/selector-group-chat.html

[17] Microsoft (2024). "GitHub Issue #4289 — SelectorGroupChat ignores selector function randomly". https://github.com/microsoft/autogen/issues/4289

[18] LangChain (2024). "Command: A new tool for building multi-agent architectures in LangGraph". https://www.langchain.com/blog/command-a-new-tool-for-multi-agent-architectures-in-langgraph

[19] LangChain (2025). "langgraph-swarm-py README". https://github.com/langchain-ai/langgraph-swarm-py

[20] (2025). "Multi-Agent Orchestration in LangGraph: Supervisor vs Swarm Tradeoffs and Architecture". dev.to. https://dev.to/focused_dot_io/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture-1b7e

[21] Li, G. et al. (2023). "CAMEL: Communicative Agents for 'Mind' Exploration of Large Language Model Society". NeurIPS. arXiv:2303.17760. https://arxiv.org/abs/2303.17760

[22] Chen, W. et al. (2023/2024). "AgentVerse: Facilitating Multi-Agent Collaboration and Exploring Emergent Behaviors". ICLR 2024. https://proceedings.iclr.cc/paper_files/paper/2024/file/578e65cdee35d00c708d4c64bce32971-Paper-Conference.pdf

[23] DeepWisdom (2024). "MetaGPT docs — agent_communication.md". https://github.com/geekan/MetaGPT-docs/blob/main/src/en/guide/in_depth_guides/agent_communication.md

[24] Qian, C. et al. (2024). "ChatDev: Communicative Agents for Software Development". ACL 2024. https://aclanthology.org/2024.acl-long.810/

[25] (2025). "LLM-based Agents Suffer from Hallucinations: A Survey of Taxonomy, Methods, and Directions". arXiv:2509.18970. https://arxiv.org/html/2509.18970v1

[26] Anthropic (2025). "How we built our multi-agent research system". Anthropic Engineering. https://www.anthropic.com/engineering/multi-agent-research-system

[27] Microsoft (2024). "Magentic-One: A Generalist Multi-Agent System". arXiv:2411.04468. https://arxiv.org/abs/2411.04468

[28] Microsoft (2024). "Magentic-One — AutoGen docs". https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html

[29] Anthropic (2025). "When to use multi-agent systems (and when not to)". Claude blog. https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them

[30] Cemri, M. et al. (2025). "Why Do Multi-Agent LLM Systems Fail?". NeurIPS 2025. arXiv:2503.13657. https://arxiv.org/abs/2503.13657

[31] Hayashi, T. et al. (2025). "An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue". IWSDS. arXiv:2501.16643. https://arxiv.org/html/2501.16643v1

[32] Zhang, R. et al. (2018). "Addressee and Response Selection in Multi-Party Conversations with Speaker Interaction RNNs". AAAI 2018. arXiv:1709.04005. https://arxiv.org/abs/1709.04005

[33] OpenAI (2024). "Orchestrating Agents: Routines and Handoffs". OpenAI Cookbook. https://developers.openai.com/cookbook/examples/orchestrating_agents

---

## 7. Methodology Appendix

### 7.1 8-Phase deep-research 파이프라인 적용

본 연구는 deep-research skill의 8단계 파이프라인을 deep 모드로 실행했다.

**Phase 1 (SCOPE)**: 연구 질문을 anygarden V1~V5 PoC에서 5회 재현된 사회자 turn-taking 결함의 정의·원인·해법으로 한정. 가정: LLM 단일 결함이 아닌 *구조적 약점의 결합*이라는 출발 가설.

**Phase 2 (PLAN)**: 3개 sub-agent 병렬 조사 각도 정의 — (a) 프레임워크 turn-taking 메커니즘 비교 (AutoGen/LangGraph/CAMEL/AgentVerse/MetaGPT/ChatDev/Magentic-One/OpenAI Swarm), (b) LLM tool-use 신뢰도·instruction decay·constrained decoding, (c) speaker selection 알고리즘·supervisor 패턴·addressee recognition.

**Phase 3 (RETRIEVE)**: 3개 general-purpose sub-agent를 `run_in_background=true`로 병렬 spawn. 각 agent는 WebSearch/WebFetch를 자율 활용해 5~10개 evidence를 구조화된 JSON으로 리턴. 총 33개 evidence row 수집.

**Phase 4 (TRIANGULATE)**: 5개 핵심 주장 각각에 3+ 독립 소스 매칭:
- Finding 1 (LLM 구조적 약점 결합): [1], [2], [3], [4], [5], [6]
- Finding 2 (프레임워크 수렴): [11], [16], [18], [19], [23], [24], [33]
- Finding 3 (constrained decoding 유일 해법): [8], [9], [10], [10b]
- Finding 4 (supervisor + fallback): [11], [27], [28], [30]
- Finding 5 (addressee recognition 약점): [31], [32], [10c]

**Phase 4.5 (OUTLINE REFINEMENT)**: 33개 evidence를 5개 finding으로 압축. 각 finding은 1개 주요 주장 + 2~5개 supporting evidence + anygarden 적용 함의 구조.

**Phase 5 (SYNTHESIZE)**: 세 축(LLM 한계 / 프레임워크 수렴 / forced compliance) 수렴 구조 도출. anygarden의 강점·공백·권장 3-tier 계층화.

**Phase 6 (CRITIQUE)**: 세 red-team 관점 — (i) Skeptical Practitioner (5회 PoC가 통계적 유의성을 갖는가? → Limitations에 명시), (ii) Adversarial Reviewer (Anthropic `tool_choice` SDK 노출 여부 미검증 → Limitations 반영), (iii) Implementation Engineer (round_robin fallback 구현 시 cycle detection과 충돌 가능성 → Recommendations에서 cycle detection 확장 명시).

**Phase 7 (REFINE)**: critique 피드백을 본문에 반영. Limitations & Caveats 섹션 강화.

**Phase 8 (PACKAGE)**: 본 markdown 보고서 + 33개 evidence가 본 보고서의 inline 인용으로 보존됨. 별도 `sources.jsonl`/`evidence.jsonl`/`claims.jsonl`은 anygarden 레포 구조에 맞춰 생략 (요청 경로가 docs/research/ 단일 markdown).

### 7.2 소스 신뢰도

Anthropic 공식 문서·블로그 [9, 26, 29], OpenAI 공식 문서 [8, 33], Microsoft Research 공식 [15, 27, 28]은 1차 출처로 신뢰도 상. arXiv 논문 [1, 2, 3, 4, 5, 6, 7, 21, 22, 25, 27, 30, 31, 32]은 채택 학회 기준으로 신뢰도 평가 — NeurIPS 2025 [30], TACL 2024 [4], ICLR 2024 [22], AAAI 2018 [32], ACL 2024 [24] 통과 건은 상, 나머지 preprint는 중상. GitHub 공식 repo·issue [12, 17, 19, 23]은 production-grade 신호로 상. 커뮤니티 블로그 [20]는 신뢰도 중, 인용 시 다른 1차 출처로 교차 검증.

### 7.3 반복성

본 연구의 sub-agent 프롬프트와 검색 각도는 본 보고서 § 7.1에 기록되어 있다. 동일 조건으로 재실행 시 2026-05-12 이후 신규 자료가 추가 편입될 수 있으며, Finding 3의 모델별 신뢰도 수치(BFCL V4)는 모델 업데이트에 민감하므로 6~12개월 주기 갱신 권장. Finding 4의 Magentic-One/AutoGen v0.4 패턴은 안정적이라 갱신 주기 더 길게 가져가도 무방.

### 7.4 anygarden 코드 인용 위치 (구현 참고용)

- `packages/agent/anygarden_agent/integrations/base.py:83` — `MessagePolicy` enum
- `packages/agent/anygarden_agent/integrations/base.py:392-680` — `decide_policy` 본체
- `packages/agent/anygarden_agent/integrations/base.py:594-599` — `next_speaker_participant_id` 검사 (rule 4a)
- `packages/agent/anygarden_agent/integrations/claude_code.py:374-424` — `handoff_to` MCP 툴 구현
- `packages/cluster/anygarden/ws/handler.py:154-221` — 서버측 speaker stamping
- `packages/cluster/anygarden/ws/handler.py:225` — `_compute_round_robin_next`
- `packages/cluster/anygarden/ws/handler.py:925-994` — `_is_ambient_candidate` + round_robin 분기
- `packages/cluster/anygarden/mcp/tools.py:155` — `create_task` MCP 툴
- `packages/cluster/anygarden/db/models.py:220-326` — Agent 모델 (`agents_md`, `model`, `collaboration_mode`)

연관 anygarden 내부 문서:
- `docs/research/2026-04-19-multi-agent-context-injection.md` — 멀티에이전트 컨텍스트 주입 결함 (별개 issue, 보완적)
- `docs/plans/2026-04-13-mention-system-design.md` — mention 시스템 설계
- `docs/plans/2026-04-19-context-injection-decoupling-design.md` — context injection 분리 설계
