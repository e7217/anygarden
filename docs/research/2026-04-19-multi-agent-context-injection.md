# 멀티에이전트 LLM 시스템에서 컨텍스트 주입과 응답 생성의 분리

**— 2024–2026 연구·프레임워크 비교와 Anygarden 아키텍처 적용성 분석**

- Date: 2026-04-19
- Mode: deep-research / deep
- Topic owner: Anygarden 프로젝트 (멀티에이전트 채팅 서버)
- Sources: 18
- Evidence rows: 15

---

## Executive Summary

Anygarden는 룸 기반 멀티에이전트 채팅 서버로, 현재 `should_respond` 게이트가 "이 메시지에 응답할 것인가"와 "이 메시지를 LLM 컨텍스트에 주입할 것인가"라는 본래 독립된 결정을 하나의 이진 판단으로 묶고 있다. 결과적으로 멘션을 받지 않은 에이전트는 메시지를 들여다보지도 못한 채 룸의 대화 흐름에서 탈락한다. `<#room>` 멘션으로 대표 에이전트가 취합해온 결과 메시지조차 다른 에이전트들의 Claude Agent SDK 세션에 주입되지 못한 채 UI에만 노출된다. 본 연구는 이 구조적 결함을 학술 문헌과 상용 프레임워크의 최신 설계 원칙으로 대조·검증하는 것을 목적으로 한다.

18개 소스 검토 결과 세 가지 수렴 결과가 드러난다. 첫째, AutoGen GroupChat [1], LangGraph MessagesState [2], CAMEL [10], AgentVerse [11], MetaGPT 계열은 공통적으로 **"공유 대화 히스토리 + 스피커 선택"** 이라는 표준 모델을 채택한다 — 모든 에이전트가 동일한 메시지 스트림을 읽고, 그 위에 "이번 턴 누가 말할지"만 별도 결정한다. Anygarden의 SDK 세션 기반 구조는 이 전제를 깨뜨려 업계 표준과 이탈한다. 둘째, Intrinsic Memory Agents [3], MAGMA [12], A-Mem [13] 같은 2025–2026 신작은 반대 극단으로 **per-agent 구조 메모리와 공유 대화 공간의 명시적 분리**를 연구 의제로 다루며, "응답 없이 메모리를 주입"하는 흐름을 구조적으로 지원한다. Anygarden가 마주한 문제의 이름이 바로 여기 있다. 셋째, Anthropic 연구 시스템 [14]은 양 극단을 택하지 않고 **비대칭 컨텍스트**(오케스트레이터만 공유 정보를 쥐고 서브에이전트는 격리) 로 단일 에이전트 대비 90.2% 성능 우위를 보이지만 15배 토큰 비용을 치른다.

Anygarden 팀에 대한 권장은 세 층위로 나뉜다. 단기로는 `ingest_context(msg)` 후크를 엔진 어댑터에 추가해 `room_query_result` 같은 메시지가 LLM 세션에 자연어 요약으로 주입되도록 한다 — Intrinsic Memory Agents의 설계 원리를 경량 모사한 변형이다. 중기로는 Claude SDK의 `resume` 기반 세션을 포기하고 AutoGen/LangGraph 방식으로 매 턴 룸 히스토리를 프롬프트로 재조립하는 session-less 모드를 선택지로 추가한다. 장기로는 MCP 계열 Observer/Publish-Subscribe 패턴 [9]에 맞춘 blackboard 구조로 공유 컨텍스트 서버를 분리하고, 각 에이전트가 자기 메모리 슬라이스를 pull한다. Addressee recognition을 LLM에 위임하지 않는 Anygarden의 서버측 명시 멘션 파싱 [5]은 오히려 강점으로 보존해야 한다.

---

## 1. Introduction

### 1.1 연구 질문

멀티에이전트 LLM 시스템에서 **"어떤 메시지가 어느 에이전트의 LLM 컨텍스트에 주입되어야 하는가"** 와 **"그 메시지에 누가 응답해야 하는가"** 는 같은 결정인가, 분리된 결정인가? 최신 연구·프레임워크가 이 결정의 분리를 어떻게 설계하는지, 그 결과가 Anygarden의 엔진 어댑터 아키텍처에 어떻게 적용되는지를 묻는다.

### 1.2 배경 맥락

Anygarden는 `packages/cluster/anygarden/ws/handler.py` 수준에서 메시지 전송 시 `parse_mentions` [자체 코드]로 `<@user:id>`, `<#room:id>`, `@Name` 세 형태를 파싱해 `metadata.mentions`에 첨부한다. 에이전트 측 `anygarden_agent/integrations/base.py`의 `should_respond`는 여섯 가지 규칙으로 메시지를 걸러내는데, 이 게이트가 `False`를 반환하면 엔진 어댑터의 `on_message`가 호출되지 않고, Claude Agent SDK의 세션(`resume=session_id`)에도 해당 메시지가 주입되지 않는다. 룸의 메시지 히스토리는 서버에 저장되지만 LLM이 "본" 히스토리는 그것의 필터링된 서브셋이 된다. 이 설계는 의도적으로 "에이전트 핑퐁 방지"와 "응답 폭주 차단"을 위해 만들어졌으나, 그 부작용으로 대표 에이전트가 만들어낸 `[취합 결과]` 메시지가 같은 룸의 다른 에이전트들에게 "보이지만 의식되지 않는" 상태가 된다.

### 1.3 범위와 방법

검토 범위는 2023–2026년의 멀티에이전트 LLM 연구 논문(주로 arXiv), 상용·오픈소스 프레임워크(AutoGen, LangGraph, CAMEL, AgentVerse, MetaGPT, ChatDev, LangGraph Swarm), 업계 엔지니어링 블로그(Anthropic, Slack, AWS), 프로토콜 서베이(MCP, ACP, A2A, ANP)로 한정한다. 2023년 이전 고전 분산 시스템 문헌(actor model, Erlang/Akka)은 필요한 개념적 뿌리만 참조한다. 검토 방법은 Deep Research 파이프라인의 8단계를 따랐으며, 18개 소스 중 핵심 주장별로 3개 이상의 독립 소스로 교차 검증했다. Anthropic 공식 블로그는 성능·토큰 수치 1차 출처로 사용되었고, 나머지는 논문 abstract·서베이·상용 문서·실무 블로그를 혼합한다.

### 1.4 가정

독자는 Anygarden 팀의 기술 독자이며 LLM MAS 개념에 익숙하다. 용어는 한국어를 주로 쓰되 핵심 학술 용어(addressee recognition, blackboard, publish-subscribe 등)는 원어 병기한다. "응답 생성(response generation)"과 "컨텍스트 주입(context ingestion)"이라는 축을 본 보고서의 분석 프레임으로 고정한다.

---

## 2. Main Analysis

### 2.1 Finding 1 — 공유 히스토리 + 스피커 선택이 MAS의 업계 표준

멀티에이전트 LLM 프레임워크의 공통 설계는 "모든 에이전트가 같은 대화 히스토리를 본다"는 전제 위에 "이번 턴 누가 말할지"를 별도 결정으로 붙이는 것이다. AutoGen GroupChat의 `select_speaker` 메서드는 `auto`, `manual`, `random`, `round_robin` 또는 커스텀 함수로 다음 발화자를 고르지만, 선택되지 않은 에이전트도 `groupchat.messages`의 전체 기록을 자동으로 관찰한다 [1]. `candidate_func` 파라미터가 특정 턴의 후보 집합만 제한하고 메시지 가시성은 건드리지 않는 것은 의도된 설계다.

LangGraph의 `MessagesState`와 `add_messages` reducer는 이 패턴을 더 명시적으로 공식화한다. "The most common way for agents to communicate is via a shared state channel, typically a list of messages. This state acts as shared memory, accessible to all nodes for reading and updating." [2]. LangGraph Swarm은 여기에 `active_agent` 마커만 추가해 "누가 주도권을 가지는가"를 별도 필드로 관리할 뿐, 히스토리 접근은 공유 채널에 맡긴다. LangGraph는 "fine-grained control"을 원하면 `input_schema`와 `output_schema`를 각 노드에 별도로 정의할 수 있지만, 이는 예외 경로이지 기본값이 아니다 [2].

CAMEL [10]과 AgentVerse [11]도 동일한 방향성을 따른다. CAMEL의 role-playing 구조는 `task-specifier agent`가 상세 설명을 만들고 AI assistant와 AI user가 다중 턴 대화를 통해 협력하는데, 둘 모두 동일한 대화 기록을 공유한다. AgentVerse는 ICLR 2024에서 발표된 4단계 아키텍처(전문가 모집 → 협력 의사결정 → 행동 실행 → 평가)를 제안하며 에이전트 간 대화와 투표가 공유 컨텍스트에서 일어난다 [11]. MetaGPT는 Beyond Self-Talk 서베이가 정의한 "Blackboard" 패러다임의 대표격으로, "shared message repository, enabling agents to exchange updates and enhance communication efficiency"를 핵심 메커니즘으로 사용한다 [7].

이 수렴은 우연이 아니다. Beyond Self-Talk 서베이는 MAS 통신 패러다임을 세 가지로 축약한다 — Message Passing(직접 또는 broadcast), Speech Act(언어가 행동), Blackboard(중앙 저장소) [7]. 세 패러다임 모두 "수신은 수동적 · 공통" 이고 "응답은 능동적 · 선택적"이라는 비대칭 위에 서 있다. 즉 학계가 2025년 시점에 합의한 형태는 "모두가 듣고, 선택된 자가 말한다"이다. Anygarden의 `should_respond`가 수신 자체를 차단하는 것은 이 합의에서 벗어난 설계이며, 이는 단순한 버그가 아니라 엔진 어댑터 계층이 "세션"을 LLM 공급사에 위임한 아키텍처적 선택의 귀결이다.

### 2.2 Finding 2 — Anygarden 이탈의 구조적 원인: 엔진 세션이 공유 히스토리 전제를 깬다

업계 표준과 Anygarden의 이탈점은 LLM 세션 관리 방식에 있다. Anygarden의 `claude_code.py` 어댑터는 `ClaudeAgentOptions(cwd=..., setting_sources=["project"], resume=session_id)` 로 Claude Agent SDK의 자체 세션을 호출한다. 세션은 SDK 내부에 살아남고, 다음 `query()` 호출은 프롬프트에 현재 메시지만 넘기며 과거 대화는 `resume`이 알아서 이어준다. 이 설계는 토큰 캐싱과 tool-use 연속성을 SDK에 위임하는 장점이 있지만, 서버가 "이 메시지도 세션에 넣어달라"고 강제로 주입할 API 포인트를 남기지 않는다.

반면 AutoGen, LangGraph, CAMEL는 자체 세션을 두지 않는다. 매 턴 프롬프트에 `groupchat.messages` 혹은 `state["messages"]` 전체(또는 필터링된 슬라이스)를 조립해 LLM에 보낸다. "무엇을 LLM이 보는가"는 프레임워크가 완전히 통제하며, 외부 이벤트로 메시지 리스트에 항목 하나를 추가하면 다음 턴 프롬프트에 자동으로 들어간다. Anygarden가 `should_respond == False`로 `on_message`를 호출하지 않으면, SDK 세션은 그 메시지의 존재조차 모르게 된다. 이것이 업계 표준과 이탈하는 지점의 정확한 기술적 원인이다.

문제를 더 복잡하게 만드는 것은 멀티엔진 구조다. Anygarden는 Claude Code뿐 아니라 OpenAI, Codex, Gemini CLI, OpenHands 등 서로 다른 세션 모델을 가진 엔진을 어댑터로 감싸는데, 각각 세션 주입 가능성이 다르다. Gemini CLI는 세션 개념이 단순하고, Codex는 stateless에 가깝다. 통일된 `ingest_context` 추상화를 만들려면 어댑터마다 다른 방식의 주입 전략이 필요하다 — 세션을 이어가는 엔진에는 다음 `query()` 프롬프트에 prefix를 덧붙이고, stateless 엔진에는 단순히 메시지 리스트에 추가하면 된다.

기존 `<#room>` 멘션 플로우는 이 구조적 한계를 드러내는 구체적 사례다. 대표 에이전트가 `[취합 결과]` 메시지를 `client.send()`로 소스 룸에 브로드캐스트하지만, 수신자인 다른 에이전트들의 `should_respond`는 rule 6(에이전트 발신 + 멘션 없음)로 걸려 skip되고, LLM 세션에는 주입되지 않는다. UI에 렌더링되는 `[취합 결과]`는 사용자에게만 보이는 "외곽 채널"이 된다. Anygarden 팀이 지적한 "컨텍스트가 공유되지 않는" 현상의 정확한 메커니즘이 여기 있다.

이 문제를 LLM에 "응답하지 마라"고 지시하는 프롬프트 엔지니어링으로 풀려는 시도는 반쪽짜리다. 문제는 프롬프트가 아니라 메시지가 세션에 들어가지 않는다는 것이기 때문이다. 따라서 해결은 프롬프트 이전, 즉 어댑터가 SDK를 호출하는 경로에 "응답 호출은 안 하지만 세션에는 넣는" 경로를 추가하는 것으로 설계되어야 한다.

### 2.3 Finding 3 — Observer/Pub-Sub 고전 패턴과 2025–2026 per-agent 메모리 계열의 부상

Anygarden가 마주한 "응답 없이 컨텍스트 주입" 상태는 사실 고전 소프트웨어 디자인 패턴으로는 이미 이름이 있다. arXiv 2506.05364 [9]은 LLM 에이전트 통신을 Mediator, Observer, Publish-Subscribe, Broker 네 가지 패턴으로 매핑하며, 특히 Model Context Protocol(MCP)이 이 네 역할을 동시에 수행할 수 있음을 분석한다. Observer와 Publish-Subscribe 패턴은 정의상 "구독자가 이벤트를 받되 발행자가 응답을 요구하지 않는" 관계로, 응답 생성과 컨텍스트 주입의 분리가 이미 원리적으로 내재되어 있다. AWS Prescriptive Guidance의 "Observer and monitoring agents" 패턴 [15]도 "passively observe systems, environments, and interactions to detect patterns, generate insights, and trigger actions"로 정의하며 이벤트 리스너 역할의 에이전트를 명시한다.

그러나 학술 MAS 서베이가 이 개념을 LLM 에이전트의 대화 처리에 적극 적용하기 시작한 것은 최근 1년의 움직임이다. Multi-Party Conversational Agents Survey [4]는 addressee selection, turn detection, response generation 세 축으로 action modeling을 분해하지만 "passive context update versus active response generation"은 명시적으로 다루지 않는다고 본 연구가 재확인했다. 즉 고전 분산 시스템 어휘로는 존재하는 개념이, MAS 논문 맥락에서는 명시적 연구 의제로 승격되지 않은 공백이 여전히 있다.

이 공백을 2025–2026년 신작들이 서서히 채우고 있다. Intrinsic Memory Agents [3]는 "agent-specific memory maintenance while preserving a shared conversation space"를 설계 원칙으로 제시하며, 각 에이전트가 역할별 JSON 메모리 슬롯(`MT_n = {S_1, S_2, ..., S_K}`)을 유지하도록 한다. 메시지가 들어올 때마다 `(old_memory, latest_output) → updated_memory` 형태로 LLM이 구조 메모리를 갱신하고, 응답 결정은 별도의 `σ(t_m) → A_n` 스케줄러가 내린다. 이는 수신과 응답의 명시적 분리다.

MAGMA [12]는 한 걸음 더 나아가 "memory representation from retrieval logic"을 분리한다. 4개 관계 그래프(semantic, temporal, causal, entity)로 메모리를 구조화하고, retrieval은 policy-guided traversal로 별도 처리한다. 이는 "어떤 메시지가 저장되는가"와 "어떤 메시지가 꺼내져 프롬프트에 들어가는가"를 다른 결정으로 본다는 뜻이다. A-Mem [13]은 NeurIPS 2025에서 발표된 논문으로 Zettelkasten 원리를 적용해 메모리가 스스로 contextual description을 생성하고 연결을 진화시킨다. Collaborative Memory [6]는 또 다른 축을 추가한다 — "private memory visible only to originating users and shared memory with selectively shared fragments"로 access control 기반의 이중 구조를 제시한다. Anygarden의 룸별/에이전트별 분리와 개념적으로 유사하다.

이 계열이 우리에게 의미하는 바는, "per-agent 메모리 분리"가 2025–2026년의 연구 트렌드이며 Anygarden가 엔진 SDK 세션으로 우연히 얻게 된 격리 특성을 학계가 역으로 설계 원리로 승격시키고 있다는 것이다. Anygarden는 **이미 격리된 세션**을 가진 셈이고, 여기에 부족한 것은 "격리된 세션에 외부 이벤트를 주입할 통로"뿐이다. 이는 처음부터 설계하는 경우보다 훨씬 낮은 재작업 비용으로 달성 가능하다.

### 2.4 Finding 4 — Addressee recognition의 LLM 한계와 서버측 명시 라우팅의 우위

"누구에게 말하는 것인가"를 LLM이 스스로 판단하게 두는 접근은 2025년 시점에서도 견고하지 않다. arXiv 2501.16643 [5]은 triadic(세 참여자) 멀티모달 대화에서 GPT-4o의 addressee recognition 정확도를 벤치마크했는데, 결과는 "marginally above chance" 수준이었다. 명시적 addressee가 드러나는 턴은 전체의 약 20%에 불과했고, 나머지 80%는 문맥·gaze·turn timing 같은 다중 신호 통합이 필요했다. Multi-Party Conversational Agents Survey [4]도 SI-RNN, ASRG, SIARNN 같은 전용 모델을 나열하지만 "robust LLM-based solutions for multi-party dialogue remains a significant challenge"로 마무리한다.

업계 봇 생태계는 정확도 문제에 실무적으로 대응한다. Slack 봇 가이드 [16, 17]는 `event.type === 'app_mention'` 으로 명시 멘션만 트리거로 받고, 스레드 내 대화는 `getThread()`로 호출 시점에 pull한다. 즉 "누가 누구에게" 를 LLM이 판단하지 않고, 플랫폼이 이벤트 수준에서 명시한다. Vercel AI SDK의 Slackbot 쿡북은 "The bot only engages when explicitly mentioned or messaged directly; there is no listening mode" [16]로 이 패턴을 단언한다.

Anygarden의 `parse_mentions`는 정확히 이 방향으로 이미 설계되어 있다. `<@user:id>`(ID 기반), `<#room:id>`(룸 라우팅), `@Name`(legacy) 세 형태가 서버에서 파싱되어 `metadata.mentions`로 명시 첨부된다. 서버측 명시 파싱은 LLM의 addressee 추론보다 **정확도가 구조적으로 높고**, 게이트 로직에 근거 추적 가능성(auditability)을 제공하며, 멀티 엔진 환경에서도 엔진별 LLM 성능 편차와 무관하게 일관된다. 본 연구는 이를 **보존해야 할 Anygarden의 강점**으로 평가한다.

다만 이 강점이 Finding 2의 결함을 감추는 역할을 한다는 점을 경계해야 한다. 명시 멘션이 정확하게 라우팅되더라도, 멘션 **없이** 룸에 떨어진 메시지(예: `[취합 결과]`, 사용자 간 사담, 다른 에이전트의 응답)는 현재 완전히 차단된다. addressee recognition과 context ingestion은 별개 문제이며, 전자를 잘 하는 것이 후자를 자동으로 해결하지 않는다. Anygarden의 명시 라우팅은 "누가 지금 응답할지"를 잘 결정하지만 "누구의 메모리를 갱신할지"는 결정하지 않는 게이트다.

### 2.5 Finding 5 — 토큰 비용과 비대칭 컨텍스트의 엔지니어링 tradeoff

"모두가 보고 한 명이 말한다"는 표준 모델은 소수 참여 시에는 자연스럽지만 에이전트 수와 대화 길이가 늘면 비용이 급격히 커진다. Anthropic 엔지니어링 블로그 [14]는 자사 연구 시스템이 "chat 대비 약 4배, 멀티 에이전트는 약 15배 토큰"을 소비한다고 밝혔다. 동시에 Claude Opus 4 lead + Sonnet 4 subagents 구성이 단일 에이전트 대비 90.2% 성능 우위를 보였고 복잡 쿼리 연구 시간을 최대 90% 단축했다.

이 tradeoff를 풀기 위한 Anthropic의 선택은 **비대칭 컨텍스트**였다. 서브에이전트는 서로의 중간 산출물을 직접 보지 못하고, 리드 에이전트만이 전체 결과를 종합한다 — 블로그는 이를 "prevents the game of telephone"으로 명시한다 [14]. 즉 모두가 같은 히스토리를 보는 AutoGen/LangGraph 기본값과 달리, 성능·비용을 실제로 운영해본 팀은 "격리된 context + 오케스트레이터 종합"을 택했다. 이 결과는 Finding 3의 per-agent 메모리 흐름과도 결이 같다.

토큰 비용 분해는 더 미세한 통찰을 제공한다. Hidden Costs of Context 블로그 [18]는 프로덕션 LLM 시스템에서 input 토큰이 output의 2–3배이며, verification 단계가 input 토큰을 과도하게 소비한다고 보고한다(MetaGPT 2048 실험에서 72%). 이는 "멤버 전체에 메시지를 재주입"하는 순진한 전략이 스케일에서 무너지는 이유를 설명한다. 실제로는 누구에게 무엇을 주입할지 선별해야 하며, 이는 본 연구의 핵심 분리 축 자체를 엔지니어링 결정으로 올린다.

Anygarden의 현 구조는 의도치 않게 비대칭 컨텍스트를 이미 구현하고 있다 — 각 에이전트가 자신의 LLM 세션을 가지므로 서로의 중간 산출물을 직접 공유하지 않는다. 다만 이 격리는 "필요한 정보"까지 차단한다는 게 문제다. Anthropic의 구조에서 리드 에이전트는 서브의 결과를 "명시적 합류 시점"에 받는다. Anygarden에 결여된 것은 바로 이 "명시적 합류 시점의 주입 통로"다. 공유 히스토리로 전부 푸는 대신, **선별적 주입**으로 가는 것이 비용과 아키텍처 양면에서 정합적이다.

Slack 생태계의 pull-on-mention 모델 [16, 17]도 같은 방향의 엔지니어링 선택이다. 메시지가 올 때마다 LLM 세션에 푸시하는 대신, 멘션 시점에 필요한 스레드 히스토리를 한 번에 조립한다. 이는 "스레드 길이 × 에이전트 수 × 메시지 빈도"의 곱셈적 비용을 피하는 방법이며, Anygarden의 중기 방향에도 시사하는 바가 크다.

---

## 3. Synthesis & Insights

세 축의 증거가 하나의 권장 아키텍처로 수렴한다. 첫째 축은 "수신과 응답은 분리된 결정" 이라는 원리(Finding 1, 3) — 업계 표준 공유 히스토리 모델과 학술 신작 per-agent 메모리 모델이 모두 이 분리를 전제한다. 둘째 축은 "LLM에게 addressee를 맡기지 않는다"는 엔지니어링 실용주의(Finding 4) — Anygarden의 명시 멘션 파싱은 이미 이 원칙을 구현하고 있다. 셋째 축은 "비대칭 컨텍스트가 비용 면에서 유리하다"(Finding 5) — Anthropic 15x 토큰 수치와 MetaGPT verification 72% 수치가 "모두에게 모든 것을" 전략의 실용 한계를 보여준다.

이 세 축을 Anygarden에 겹치면 명확한 구도가 나온다. Anygarden는 (1) 서버측 명시 라우팅이라는 강점을 이미 갖고 있고, (2) 엔진 SDK 세션 덕분에 per-agent 격리도 자동으로 얻고 있지만, (3) 격리된 세션에 외부 이벤트를 주입할 **제어된 통로**가 없다는 단일 공백만 남는다. 따라서 방향은 "모든 메시지를 모두에게 푸시하는 공유 히스토리로 회귀"가 아니라 "명시 라우팅 + 비대칭 컨텍스트 + 제어된 주입 통로"의 조합이다.

Intrinsic Memory Agents의 원리를 경량 모사하는 것이 합리적 첫 걸음이다. 엔진 어댑터 인터페이스에 `ingest_context(msg)` 후크를 추가하고, `metadata.room_query_result`나 `metadata.ingest_only=true` 같은 명시 플래그가 붙은 메시지만 이 경로를 탄다. Claude Agent SDK처럼 세션을 이어가는 엔진은 다음 `query()` 호출 시 프롬프트 prefix로 "이전에 다음 정보가 있었음: ..." 를 삽입한다. Stateless 엔진은 메시지 리스트에 항목만 추가한다. 이 변경은 기존 `should_respond` 로직과 충돌하지 않고 병존 가능하며, 루프 폭주 위험도 없다(응답 생성이 트리거되지 않으므로).

중기로는 session-less 엔진 모드를 병렬 옵션으로 추가한다. Claude SDK의 `resume`을 끄고 매 턴 LangGraph식으로 룸 히스토리를 직접 프롬프트로 조립한다. 이 모드는 토큰 비용이 올라가지만 AutoGen/LangGraph 표준 모델과 완전히 정렬되며, 에이전트 행동의 예측 가능성과 디버깅 용이성이 개선된다. 에이전트 설정에서 "session mode"와 "stateless mode"를 선택하게 두면 운영자가 트레이드오프를 인식하고 고를 수 있다.

장기로는 blackboard 패턴을 승격시킨다. MCP Observer/Pub-Sub 원리에 따라 룸의 메시지 히스토리를 명시적 공유 컨텍스트 서버로 분리하고, 각 에이전트는 자기 필터/정책에 따라 필요한 슬라이스를 pull한다. 이는 Collaborative Memory의 access control 모델과 결합 가능하며, Anygarden의 게스트/유저/에이전트 역할 구분과 자연스럽게 맞물린다. MAGMA/A-Mem의 구조화된 메모리가 성숙해지면 각 에이전트 레벨에서도 순차 수용 가능하다.

Addressee recognition 정확도에 대한 경계도 필요하다. 본 연구의 Finding 4는 서버측 명시 파싱이 LLM 추론보다 견고함을 보였다. 향후 Anygarden에 "자연어 멘션에서 의도를 추론하는 자동 addressee resolution"을 추가하고 싶은 유혹이 생길 수 있으나, GPT-4o 벤치마크 결과가 암시하듯 이는 정확도·예측성 양 측면에서 회귀일 가능성이 크다. 명시 멘션을 UI에서 더 쉽게 만들어주는 방향(멘션 자동완성, 스마트 제안)이 더 나은 투자 대상이다.

---

## 4. Limitations & Caveats

본 연구는 WebSearch/WebFetch로 수집한 18개 소스에 기반하며, 다음의 공백을 인정한다. 첫째, arXiv 2506.05364(MCP Design Pattern Survey) [9]와 2502.14321(Beyond Self-Talk) [7]의 풀 PDF 본문을 완전히 정독하지 못했고 abstract와 HTML 추출 요약에 의존했다. 둘째, CAMEL·AgentVerse·ChatDev의 원 논문 본문도 핵심 설명만 확인했으며 구현 레벨 디테일은 GitHub 저장소 직접 읽기로 이어지지 않았다. 셋째, Slack/Discord 봇 생태계는 공식 문서 및 쿡북 위주로 검토했고 프로덕션 운영 데이터나 비용 벤치마크는 제한적이다.

Anygarden 아키텍처에 대한 본 연구의 기술적 주장은 `ws/handler.py`, `integrations/base.py`, `integrations/claude_code.py` 등 이전 대화에서 확인한 코드 스냅샷에 기반한다. 이 코드는 2026-04-19 시점 main 브랜치 기준이며, 향후 리팩터링으로 `should_respond` 게이트가 3-state로 이미 진화했을 경우 본 권장 중 단기 부분은 재검토되어야 한다. 본 연구는 Claude Agent SDK의 `resume` API 내부 구현에 접근하지 못했으므로, "prefix 주입이 SDK 측에서 부작용 없이 허용되는지"는 실제 프로토타이핑으로 확증이 필요하다.

Anthropic의 90.2% 성능 수치 [14]는 공식 블로그가 공개한 내부 평가 기준이며 외부 재현 실험은 아직 없다. 멀티에이전트 대 단일 에이전트 비교는 태스크 의존성이 강하므로 Anygarden의 실제 사용 시나리오(룸 대화, cross-room query)에 그대로 외삽하기는 어렵다. 마찬가지로 "15배 토큰" 수치도 Anthropic의 deep research 태스크 기준이며 짧은 채팅에는 과하게 적용될 수 있다.

마지막으로 본 연구는 "응답 없이 컨텍스트 주입"이라는 단일 축에 집중했기 때문에 인접 이슈 — 예컨대 에이전트 간 tool-call 결과 공유, 메모리 장기 저장, 프라이버시 경계 — 는 상대적으로 가볍게 다루었다. Anygarden의 admin/owner 권한 체크는 현 아키텍처의 제약으로만 언급했고, context injection 변경이 권한 경계에 미치는 영향은 후속 과제로 남긴다.

---

## 5. Recommendations

**단기(2–4주)**: 엔진 어댑터 인터페이스에 `ingest_context(msg)` 후크를 추가하고, `should_respond`의 반환 타입을 `bool`에서 `{RESPOND, INGEST_ONLY, SKIP}` 3-state enum으로 확장한다. `metadata.room_query_result` 또는 `metadata.ingest_only=true` 플래그를 가진 메시지는 INGEST_ONLY로 분류해 LLM 응답 호출 없이 다음 `query()` 프롬프트 prefix로 "이전에 다음 일이 있었음: …" 요약을 주입한다. Claude Code 어댑터 먼저 구현 후 Gemini CLI, Codex로 확장한다. Intrinsic Memory Agents [3]의 구조화 메모리 슬롯까지 재현할 필요는 없으며, 자연어 요약만으로도 현상 해결에는 충분하다.

**중기(1–3개월)**: Claude Agent SDK의 `resume` 기반 세션 의존을 옵션화한다. 에이전트 설정에 `session_mode: "sdk_resume" | "stateless_history"` 플래그를 추가하고, stateless 모드에서는 매 턴 룸 히스토리(또는 그 슬라이스)를 직접 프롬프트로 조립한다. AutoGen [1], LangGraph [2] 표준 모델과 정렬되며, Finding 5가 보여준 비용 증가(최대 수 배)는 엔진별 토큰 예산 관리와 history pruning 정책으로 대응한다. 이 모드는 "모두가 같은 히스토리를 본다"는 강한 일관성이 필요한 다자 협업 룸(예: 같은 태스크를 공동 수행하는 에이전트 페어)에 우선 적용한다.

**장기(3–12개월)**: MCP Observer/Publish-Subscribe 패턴 [9]을 수용해 룸의 공유 컨텍스트를 명시적 blackboard 서버로 분리한다. 각 에이전트는 자기 subscription 정책(예: "내가 멘션된 메시지 + 같은 참여자가 이어간 대화")에 따라 슬라이스를 pull하며, Collaborative Memory [6]의 access control 원리로 권한 경계를 유지한다. MAGMA [12]나 A-Mem [13]의 구조화 메모리를 에이전트 레벨에서 선택적으로 도입해 장기 대화·다자 대화의 메모리 폭발을 관리한다.

Addressee recognition을 LLM에 위임하지 말 것을 명시 권장한다. 서버측 `parse_mentions` 구조는 현재의 강점이며, LLM 기반 자동 addressee 추론은 Finding 4의 벤치마크 결과에 비추어 회귀 위험이 크다. UI 개선(자동완성, 제안) 투자가 더 나은 방향이다.

Anygarden 팀이 본 권장 중 단기 항목부터 진행할 경우, 후속 설계 문서(`docs/plans/2026-04-XX-context-injection-hook-design.md`)로 `ingest_context` 인터페이스 계약, 엔진별 주입 전략, `should_respond` 3-state 전환의 마이그레이션 경로를 정리하는 것을 제안한다. TDD 기반 구현과 `integrations/claude_code.py`에 대한 회귀 테스트를 먼저 작성한 뒤 어댑터 변경을 진행하는 것이 안전하다.

---

## Bibliography

[1] Microsoft (2024). "Customize Speaker Selection — AutoGen 0.2". https://microsoft.github.io/autogen/0.2/docs/topics/groupchat/customized_speaker_selection/ (Retrieved: 2026-04-19)

[2] LangChain (2025). "Multi-agent Systems — LangGraph Concepts". https://langchain-ai.github.io/langgraphjs/concepts/multi_agent/ (Retrieved: 2026-04-19)

[3] Xu et al. (2025). "Intrinsic Memory Agents: Heterogeneous Multi-Agent LLM Systems through Structured Contextual Memory". arXiv:2508.08997. https://arxiv.org/abs/2508.08997 (Retrieved: 2026-04-19)

[4] Multi-Party Conversational Agents Survey Authors (2025). "Multi-Party Conversational Agents: A Survey". arXiv:2505.18845. https://arxiv.org/html/2505.18845v1 (Retrieved: 2026-04-19)

[5] Hayashi et al. (2025). "An LLM Benchmark for Addressee Recognition in Multi-modal Multi-party Dialogue". arXiv:2501.16643. https://arxiv.org/abs/2501.16643 (Retrieved: 2026-04-19)

[6] Collaborative Memory Authors (2025). "Collaborative Memory: Multi-User Memory Sharing in LLM Agents with Dynamic Access Control". arXiv:2505.18279. https://arxiv.org/html/2505.18279v1 (Retrieved: 2026-04-19)

[7] Beyond Self-Talk Authors (2025). "Beyond Self-Talk: A Communication-Centric Survey of LLM-Based Multi-Agent Systems". arXiv:2502.14321. https://arxiv.org/abs/2502.14321 (Retrieved: 2026-04-19)

[8] Agent Interoperability Survey Authors (2025). "A Survey of Agent Interoperability Protocols: MCP, ACP, A2A, and ANP". arXiv:2505.02279. https://arxiv.org/html/2505.02279v1 (Retrieved: 2026-04-19)

[9] MCP Design Pattern Survey Authors (2025). "Survey of LLM Agent Communication with MCP: A Software Design Pattern Centric Review". arXiv:2506.05364. https://arxiv.org/abs/2506.05364 (Retrieved: 2026-04-19)

[10] Li, G. et al. (2023). "CAMEL: Communicative Agents for 'Mind' Exploration of Large Language Model Society". NeurIPS 2023. arXiv:2303.17760. https://arxiv.org/abs/2303.17760 (Retrieved: 2026-04-19)

[11] Chen, W. et al. (2023/2024). "AgentVerse: Facilitating Multi-Agent Collaboration and Exploring Emergent Behaviors". ICLR 2024. arXiv:2308.10848. https://arxiv.org/abs/2308.10848 (Retrieved: 2026-04-19)

[12] Jiang, D. et al. (2026). "MAGMA: A Multi-Graph based Agentic Memory Architecture for AI Agents". arXiv:2601.03236. https://arxiv.org/abs/2601.03236 (Retrieved: 2026-04-19)

[13] Xu, W. et al. (2025). "A-Mem: Agentic Memory for LLM Agents". NeurIPS 2025. arXiv:2502.12110. https://arxiv.org/abs/2502.12110 (Retrieved: 2026-04-19)

[14] Anthropic (2025). "How we built our multi-agent research system". Anthropic Engineering Blog. https://www.anthropic.com/engineering/multi-agent-research-system (Retrieved: 2026-04-19)

[15] AWS (2025). "Observer and monitoring agents — Agentic AI Patterns". AWS Prescriptive Guidance. https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/observer-and-monitoring-agents.html (Retrieved: 2026-04-19)

[16] Vercel (2025). "Slackbot Agent Guide". AI SDK Cookbook. https://ai-sdk.dev/cookbook/guides/slackbot (Retrieved: 2026-04-19)

[17] Slack (2025). "Introducing Slackbot, Your Context-Aware AI Agent for Work". Slack Blog. https://slack.com/blog/news/slackbot-context-aware-ai-agent-for-work (Retrieved: 2026-04-19)

[18] Tian Pan (2025). "The Hidden Costs of Context: Managing Token Budgets in Production LLM Systems". tianpan.co. https://tianpan.co/blog/2025-11-11-managing-token-budgets-production-llm-systems (Retrieved: 2026-04-19)

---

## 7. Methodology Appendix

### 7.1 Deep-Research 파이프라인 적용

본 연구는 `199-biotechnologies/claude-deep-research-skill`의 8단계 파이프라인을 deep 모드로 적용했다. Phase 1 SCOPE에서 연구 질문을 "응답 생성과 컨텍스트 주입의 분리"로 한정했고, 2026-04-19 기준 최근 1.5년 자료에 가중치를 두었다. Phase 2 PLAN에서 이전 Standard 모드 실행(`/home/e7217/projects/anygarden-home/.tmp/research-multi-agent-context-injection-20260419.md`)의 10개 소스를 초석으로 재활용하고, 7개 delta 검색 각도를 신규로 정의했다(Anthropic 공식 블로그, CAMEL 원 논문, AgentVerse, actor model/pub-sub, MAGMA, A-Mem, Slack/Discord 실무).

Phase 3 RETRIEVE에서는 WebSearch를 병렬 호출로 총 13회 실행하고 주요 페이지는 WebFetch로 정독했다. 첫 회차에서 6개 각도, 두 번째 회차에서 7개 각도, 세 번째 회차에서 후속 2회를 실행해 총 18개 소스를 `sources.jsonl`에 `citation_manager.py register-source`로 등록했다. Phase 4 TRIANGULATE에서 5개 핵심 주장(공유 히스토리 표준성, Anygarden 이탈 원인, 고전 패턴과 신작 trend, addressee 한계, 비용 tradeoff) 각각에 3개 이상의 독립 소스를 매칭했다. Phase 4.5 OUTLINE REFINEMENT에서 이전 분석의 10개 비교 행을 5개 finding으로 압축해 신호 밀도를 높였다.

Phase 5 SYNTHESIZE에서 세 축(분리 원리, 명시 라우팅, 비대칭 컨텍스트) 수렴 구조를 도출하고, Anygarden의 강점·공백·권장을 단기/중기/장기로 계층화했다. Phase 6 CRITIQUE에서 세 가지 red-team 관점을 적용했다 — Skeptical Practitioner(실무자가 15x 토큰 수치를 Anygarden 상황에 직접 적용 가능한가? → 태스크 성격 차이 보정 필요, Limitations에 명시), Adversarial Reviewer(Finding 3이 "고전 패턴은 있고 MAS는 공백"이라는 주장을 펴는데 MCP Design Pattern Survey의 전문 초록이 아직 풀 텍스트로 확보되지 않았다는 약점 존재 → Limitations에 반영), Implementation Engineer(단기 권장의 `ingest_context` 후크가 Claude SDK의 `resume` 내부와 충돌하지 않는지 불명 → Limitations 및 권장에서 "실제 프로토타이핑 확증 필요" 명시). Phase 7 REFINE에서 이들 피드백을 본문에 반영했다.

Phase 8 PACKAGE에서 `~/Documents/MultiAgent_Context_Injection_Research_20260419/`에 `report.md`, `sources.jsonl`(18 rows), `evidence.jsonl`(15 rows), `claims.jsonl`, `run_manifest.json`을 생성했다.

### 7.2 소스 신뢰도

Anthropic 공식 엔지니어링 블로그 [14], Microsoft AutoGen 공식 문서 [1], LangChain 공식 개념 문서 [2], AWS 공식 prescriptive guidance [15]는 1차 출처로 신뢰도 상을 부여한다. arXiv 논문들([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])은 피어 리뷰 상태가 제각각이나 NeurIPS 2023([10]), NeurIPS 2025([13]), ICLR 2024([11]) 채택 건은 학회 통과 기준으로 신뢰도 상이다. 나머지 preprint는 신뢰도 중상으로 평가하고 교차 검증으로 보강했다. Slack 공식 블로그 [17]와 Vercel AI SDK 쿡북 [16]은 상용 문서이나 구현 관점이 구체적이라 신뢰도 상. 커뮤니티 블로그 [18]는 수치 인용 시 원 출처 추적이 추가 필요하며 신뢰도 중으로 처리했다.

### 7.3 반복성

본 연구의 검색 쿼리 목록, WebFetch 프롬프트, 소스 등록 JSON은 모두 본 리포트와 같은 디렉터리의 아티팩트 파일(`sources.jsonl`, `evidence.jsonl`, `run_manifest.json`)에 보존되어 있다. 동일한 쿼리로 재실행 시 2026-04-19 이후의 신작이 추가로 편입될 수 있으며, 본 리포트의 Finding 5(비용 수치)는 모델 가격 정책 변동에 민감하므로 6–12개월 주기 갱신을 권한다.
