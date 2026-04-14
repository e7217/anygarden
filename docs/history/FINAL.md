# FINAL — Doorae 채팅 서버 최종 설계 종합 보고서

**작성자**: final design critic
**작성일**: 2026-04-07
**대상**: Doorae 멀티 에이전트 채팅 서버 설계 리더십
**분량 목표**: 2,000~3,000 단어 (실행 가능한 결정 지원)

---

## 1. 배경 및 과정

Doorae는 여러 에이전트 런타임 엔진(Claude Code SDK, Codex, OpenHands, Deep Agents)이 한 채팅 서버 위에서 협업하도록 만드는 경량 메시징 허브다. 초기 탐색 단계에서 9개 에피소드(EP00~EP10)가 각각 서로 다른 기술 스택과 아키텍처 선택으로 작성되었으며, 그 결과 WebSocket·NATS·gRPC·SSE+REST·GraphQL·4-프로토콜 하이브리드 등 6개 주요 방향이 병렬로 존재하게 되었다.

이 다양성을 정리하기 위해 다음 4단계 프로세스가 진행되었다:

1. **R0 — 구성 요소 분해**: Component Analyst가 9개 에피소드를 10개 설계 차원(D1 채팅 프로토콜 ~ D10 관찰 편의성)으로 분해해 `00-component-matrix.md`를 작성했다. 이 매트릭스에서 "단순 MVP / 프로덕션 엔터프라이즈 / 최대 유연" 3개 방향이 도출되었다.
2. **R0 — 초안 작성**: architect-A는 안 A(Simple), architect-B는 안 B(Production), architect-C는 안 C(Flexible)를 병렬 설계했다.
3. **R1·R2·R3 — 교차 리뷰**: final design critic이 세 차례 교차 리뷰를 진행했다. R1에서 치명적 이슈 2건(안 B 다중 인스턴스 SSE 팬아웃 부재, 안 C Phase 1 MessageBus 자기 모순)을 발견하여 해결했고, R2에서 크로스 일관성·관측성 SLA를 보강했으며, R3(`feedback-r3.md`)에서 모든 지시가 반영되었는지를 최종 검수했다.
4. **R3 판정**: 3개 안 모두 production-ready로 판정되었고, 본 FINAL.md가 통합 결정 문서 역할을 맡는다.

3개 안은 **서로 대체하는 선택지가 아니라, 서로 다른 조직 환경·규제 압력·운영 역량에 대응하는 독립적인 최적해**이다. 이 점이 본 보고서 전반의 전제다.

---

## 2. 3개 안 개요 표

| 항목 | **안 A (Simple)** | **안 B (Production)** | **안 C (Flexible)** |
|---|---|---|---|
| **한 줄 요약** | 단일 프로세스·단일 프로토콜·단일 DB. 팀 10명 이하 MVP | Event Store 1급·감사 완벽·시간 여행. Regulated 환경 전용 | 4종 프로토콜·NATS 백본·Phase gating. 이종 스택과 수평 확장 |
| **주 프로토콜** | WebSocket (JSON) | SSE + REST POST | WS + SSE+REST + gRPC + NATS 직접 |
| **저장 방식** | PostgreSQL `messages` 테이블 + `seq` | PostgreSQL Event Store (append-only, immutable) | PostgreSQL `messages` 테이블 + Phase 5 선택적 Event Store |
| **실시간 팬아웃** | 단일 프로세스 (수직 확장) | PostgreSQL `LISTEN/NOTIFY` (p99 150ms) | NATS JetStream durable consumer (Phase 4+) |
| **감사 강도** | 기본 로그 | **최강 (UPDATE/DELETE 권한 자체 없음)** | 기본 로그 + 선택적 Event Store (Phase 5) |
| **페더레이션** | 없음 | 선택적 Matrix (Tier 2, ~280 LOC) | NATS 클러스터 공유 (기본) + 선택적 Matrix (~300 LOC) |
| **LOC (필수/+옵션1/상한)** | ~710 / ~790 / ~960 | ~1,130 / ~1,410 / ~1,720 | ~1,120 / ~1,420 / ~1,540 (Phase 4 기준) |
| **권장 규모** | ~50 Room, ~10 msg/s | ~500 Room, ~50 msg/s | 수천 Room, 수백 msg/s (Phase 4+) |
| **운영 역량** | FastAPI + PostgreSQL | + DB 권한 분리, LISTEN/NOTIFY 튜닝 | + NATS 클러스터, K8s 운영 |
| **최종 Status** | **ready** | **ready** (SLA 실측 권장) | **ready (Phase 1~3)** / **ready-with-followup (Phase 4+)** |

---

## 3. 10개 차원 × 3개 안 매트릭스

`00-component-matrix.md`가 정의한 10개 설계 차원에 3개 안의 최종 선택을 매핑했다. 셀 값은 선택한 구체적 기술이다.

| # | 차원 | 안 A | 안 B | 안 C |
|---|---|---|---|---|
| **D1** | 채팅 프로토콜 | WebSocket (JSON) | **SSE + REST POST** (curl 가능) | WS + SSE+REST + gRPC + NATS 동시 |
| **D2** | 메시지 영속화 | `messages` 테이블 + `seq` | **Append-only Event Store** (immutable) | `messages` 테이블 (+ Phase 5 Event Store 옵션) |
| **D3** | 수평 확장 | 없음 (단일 프로세스) | **PG `LISTEN/NOTIFY`** 다중 인스턴스 | **NATS JetStream** durable consumer |
| **D4** | 실시간 푸시 | WebSocket 양방향 | SSE 단방향 + Last-Event-ID | 프로토콜별 네이티브 푸시 |
| **D5** | 페더레이션 | 없음 | Matrix 선택 (Tier 2) | NATS 클러스터 (기본) + Matrix 옵션 |
| **D6** | 배포 단위 | 단일 바이너리 (uvicorn + SQLite/PG) | systemd/Docker/K8s (에이전트 = 컴플라이언스 격리 단위) | K8s Pod (어댑터별 리소스 분리) |
| **D7** | 클라이언트 지원 | 브라우저 WS / JS 클라 | **curl / fetch / EventSource** 모두 | 환경별 최적 프로토콜 선택 |
| **D8** | 에이전트 SDK 통합 | 공통 `doorae-sdk` (WS) | 공통 `doorae-sdk` (SSE+REST) | 런타임 × 프로토콜 매핑 (§8.1) |
| **D9** | 재연결/복구 | `since_seq` 쿼리 (WS) | **Last-Event-ID** (HTTP 표준) | 프로토콜별 + NATS durable offset |
| **D10** | 디버깅 편의성 | 중간 (wscat 필요) | **최상** (curl/브라우저 DevTools) | 프로토콜별 (curl/grpcurl/nats sub) |

**강조(굵은 글씨)**: 각 안이 해당 차원에서 경쟁 안 대비 가장 강한 선택을 한 지점이다. 안 A는 어느 차원에서도 1등이 아니지만 **전 차원에서 최소 복잡도**를 유지한 것이 차별점이다.

---

## 4. 선택 가이드 (결정 트리)

### 4.1 결정 트리

```
Q1. 법적/규제 감사 요구가 있는가?
    (SOX / MiFID II / HIPAA / SOC 2 / GDPR Art. 17 / 21 CFR Part 11 /
     내부 감사팀이 "LLM의 어떤 결정이 왜 내려졌는가"를 추적해야 함)
│
├─ 예 ───────────────────────────────────────────────┐
│                                                    │
│   ──> 안 B 확정                                    │
│       (Event Store 1급, doorae_audit SELECT-only,  │
│        시간 여행 쿼리, LISTEN/NOTIFY 팬아웃)       │
│                                                    │
│   ※ 주의: 이 경로에서 "다중 프로토콜도 필요"라면  │
│          구조적으로 충족 불가 (§7 참조)            │
│                                                    │
└──────────────────────────────────────────────────-─┘
│
└─ 아니오
    │
    Q2. 한 Room 안에 서로 다른 프로토콜이 공존해야 하는가?
        (모바일=WS, 웹=SSE, 백엔드=gRPC, 외부 워커=NATS 직접;
         또는 런타임별 선호 프로토콜이 다름)
    │
    ├─ 예 ──────────────────────────────────────┐
    │                                            │
    │   ──> 안 C (Phase 1부터 시작)              │
    │       필요 시 Phase 3·4·5 단계적 확장      │
    │                                            │
    └───────────────────────────────────────────-┘
    │
    └─ 아니오
        │
        Q3. 팀 규모 / 운영 역량은?
        │
        ├─ 팀 ≤10명, NATS/분산 시스템 경험 없음
        │   ──> 안 A
        │
        ├─ 팀 수십 명, 단일 프로토콜로 충분하나
        │  다중 인스턴스 운영 가능성 있음
        │   ──> 안 C Phase 1~3 (InProcessMessageBus)
        │       또는 안 A (세로 확장이면 충분)
        │
        └─ 팀 수십 명+, 다중 인스턴스 + 여러 프로토콜
           수평 확장이 필수
            ──> 안 C Phase 4+ (NATSMessageBus 전환)
```

### 4.2 결정 트리 사용 주의사항

1. Q1이 "예"면 Q2·Q3는 무의미하다. 감사 1급 요구는 프로토콜 다양성과 구조적으로 공존할 수 없다 (§7 공백 2 참조).
2. Q2·Q3의 "안 A vs 안 C Phase 1~3" 경계 영역에서 결정 요인은 **운영팀이 Phase 4 전환을 미래 옵션으로 유지하고 싶은가**이다. 예이면 안 C, 아니면 안 A.
3. Q3에서 "팀 수십 명 + 단일 프로토콜 + 감사 없음"은 **안 A가 충분하다**. 안 C Phase 1~3이 과잉이 될 수 있다.

---

## 5. 각 안 요약

### 5.1 안 A — Simple (약 600 단어)

**목표**: 10명 이하 팀이 FastAPI + SQLAlchemy + PostgreSQL만으로 에이전트 협업 채팅을 붙일 수 있도록, "필수 ~710 / 필수+옵션1 ~790 / 상한 ~960 LOC"의 최소 표면적을 유지하는 것이다. SQLite로 시작해 트래픽이 늘면 PostgreSQL로 전환하는 경로가 기본 가정이다.

**핵심 결정**:
- 단일 프로토콜 **WebSocket**. 브라우저 네이티브이며, `doorae-sdk` 한 벌로 에이전트 쪽 통합이 완결된다.
- 메시지는 `messages` 테이블에 append + `seq` 컬럼으로 단조 증가. 재연결은 `since_seq` 쿼리 한 방.
- 7개 공통 엔티티 기본 스키마만 사용하며, Room은 `parent_room_id + is_dm` 원자 속성으로 서브 채널과 DM을 표현한다 (R1에서 정규화 완료).
- SQLite→PG 전환은 "환경변수 한 줄"이 아닌 `seq` 재발급 경합 이슈를 동반하며, R1 개정본 §4.3에서 `advisory_lock` + `MAX(seq)+1` 자연 승계 또는 BIGSERIAL `setval` 1회 재시드 2가지 경로를 명시했다.
- 서브에이전트는 `Room.parent_room_id` 관계로만 표현한다. MCP 프레이밍은 사용하지 않는다.

**강점**: 코드가 적고 운영 표면이 좁아 학습 비용이 최소다. 어떤 조직도 3~5일 내에 배포까지 도달할 수 있다. 다른 안으로의 마이그레이션 경로가 문서화되어 있어 "나중에 바꾸면 된다"는 선택지가 실재한다.

**약점**: 수평 확장이 없다. 초당 10건·Room 50개를 넘으면 세로 확장이 먼저 시도되고 그 다음엔 안 C 또는 안 B로 마이그레이션해야 한다. 감사 추적은 일반 로그 수준이며 규제 환경에서는 사용 불가다.

**언제 고르는가**: 스타트업 초기 / 내부 도구 / 오픈소스 프로젝트의 기본 선택지. "언젠가 Phase 4로 갈 수도 있다"는 여지를 두지 않는 팀에게 최적이다.

---

### 5.2 안 B — Production (약 680 단어)

**목표**: 금융·의료·법률처럼 "모든 메시지와 모든 상태 변경이 immutable 이벤트로 보존되어야 하고, 감사관이 임의 시점의 시스템 상태를 SQL 한 줄로 재현할 수 있어야 하는" 환경에 대응한다. Event Store가 **옵션이 아닌 1급 구조**라는 점이 안 B의 정체성이다.

**핵심 결정**:
- 단일 프로토콜 **SSE + REST POST**. `curl` 한 줄로 발신·수신·시간 여행 쿼리가 모두 가능하며, 이는 감사 투명성의 필수 요건이다 (§4 원칙). WebSocket은 디버깅 비용과 프록시 호환성 문제로 명시적으로 배제했다 (§15.3).
- 저장은 **Append-only `events` 테이블**이며, `doorae_app` 계정에는 INSERT만, `doorae_audit` 계정에는 SELECT만 부여한다. UPDATE/DELETE 권한 자체가 존재하지 않는다.
- R1에서 발견된 치명적 이슈(다중 인스턴스 SSE 팬아웃 부재)는 **PostgreSQL `LISTEN/NOTIFY`** 기반 `DistributedSSEBroadcaster`(~180 LOC)로 해결되었다. 외부 브로커(Redis/NATS)를 도입하지 않은 이유는 "진실원 1개, 트랜잭션 원자성, 운영 단순"이라는 감사 철학 정합성이다 (§6.4).
- R2에서 LISTEN/NOTIFY DB 재조회 증폭 현상을 3개 지표로 관측 가능하게 만들었고(`doorae_sse_refetch_per_notify`, `_notify_to_delivery_ms`, `_db_reread_count`), NOTIFY 전달 SLA를 단일 p99 50ms / 분산 p99 150ms / E2E p99 200ms로 수치화했다.
- 에이전트 격리는 systemd / Docker / K8s 중 조직 성숙도에 맞춰 선택하며, K8s Pod는 "에이전트 1개 = 컴플라이언스 격리 단위"라는 **안 C와 다른 의미**를 가진다 (§9.3.1 차별성 표).
- 페더레이션은 Matrix가 Tier 2 옵션이며, 코드가 조건부 로딩되어 기본 배포에서는 `import`조차 되지 않는다 (§11.5).

**강점**: 감사 증거 능력이 최상이다. 시간 여행 쿼리는 SQL 한 줄이며, 사후 분석·컴플라이언스 조사·포스트모템이 무료다. 단일 프로토콜(SSE+REST)은 감사 무결성의 직접적 근거이다. 4개 엔진(Claude Code/Codex/OpenHands/Deep Agents)이 모두 같은 인터페이스에 붙는다.

**약점**: Event Store의 읽기 비용과 DB 권한 분리 관리 부담이 있다. 감사 요구가 없는 조직에게는 과잉이다. LISTEN/NOTIFY SLA는 설계 기준값이므로 실제 인스턴스 수 / payload 크기 / PG 버전에 따라 구현 단계 실측이 권장된다. LOC 상한 ~1,720이 3개 안 중 가장 크다.

**언제 고르는가**: 규제 감사 요구가 명확한 환경. 예: 금융 거래 봇, 의료 진단 보조, 법률/회계 기록 보존, SOC 2 Type II 인증 준비, GDPR Art. 17 삭제 요청 추적. 감사 요구가 "언젠가 생길 수도 있다"면 안 C Phase 5 Option B가 아니라 **처음부터 안 B**를 택하는 편이 경제적이다.

---

### 5.3 안 C — Flexible (약 700 단어)

**목표**: 모바일·웹·백엔드·외부 워커가 각자 다른 프로토콜로 같은 Room에 접속해야 하며, 시작은 작게 하되 수평 확장이 1급 목표여야 하는 조직을 위한 설계다. **Phase 1→5의 gating이 핵심 설계 장치**이며, 작은 팀이 처음부터 NATS를 띄우지 않고 시작할 수 있게 한다.

**핵심 결정**:
- **4종 어댑터**: WebSocket / SSE+REST / gRPC bidi / NATS 직접 구독. 4개 어댑터가 같은 내부 버스(`MessageBus`)를 공유하며, 클라이언트가 환경에 맞는 프로토콜을 선택한다.
- **R1 치명적 이슈 해결**: 초안에서 `MessageBus`가 `NatsTransport`를 요구하는 바람에 "Phase 1은 NATS 없음"이 자기 모순이었다. R1 개정본 §4.2에서 Python `Protocol`로 인터페이스를 분리하고 `InProcessMessageBus` (Phase 1~3, asyncio.Queue 기반) / `NATSMessageBus` (Phase 4~5, JetStream 기반) 2구현체를 만들어 DI(`build_message_bus(config)`)로 선택한다. 설정 한 줄로 Phase 4 전환이 가능하다.
- **Room의 원자 속성화**: R2에서 `kind: chat|channel` 파생 필드를 삭제하고 안 A와 동일한 `parent_room_id + is_dm` 원자 속성으로 통일했다 (§5.1).
- **Machine ≠ 서버 인스턴스** 분리: 초안에서 "Machine.instance_id = 버스 source_instance_id"로 혼동했던 것을 R2에서 정정했다. `Machine`은 에이전트 호스트, `doorae_instance_id`는 서버 프로세스이며 `source_server_instance_id`로 필드명을 정규화했다.
- R2에서 **Phase 4 전환 드레인/dedupe 창 수치**를 구체화했다: `drain.timeout_sec=60`, `grace_period_sec=10`, `hard_kill_timeout_sec=90`, dedupe window 30초 + clock skew 5초, LRU 10K entries (§14.2.3).
- **프로토콜별 느린 구독자 의미론**이 §6.5에 표로 정리되어 있다: WS fast-fail (CloseCode 1013) / SSE queue-close / gRPC flow control (자연 백프레셔) / NATS store-forward (durable consumer).
- K8s manifest는 어댑터별로 4종이며(§8.8), 리소스 프로파일이 서로 다르다 (WS = 연결 수 많음, gRPC = CPU 편중, SSE = 많은 연결 + 짧은 요청, NATS = 경량).

**강점**: 이종 프로토콜과 수평 확장이 1급이다. Phase gating 덕분에 초반 복잡도가 Phase 1 기준 ~450~800 LOC로 안 A와 비슷하며, Phase 4 전환 시 어댑터 코드를 고칠 필요가 없다. 이종 스택 조직이 점진적으로 확장할 수 있다.

**약점**: 4종 프로토콜은 테스트 매트릭스가 폭발한다. NATS 운영 경험이 필요하며 Phase 4 전환은 단순하지 않다. R2에서 자진 안내(§17.5)로 "Phase 4로 가지 않을 팀은 안 A를 선택하라"고 명시했다. **감사 1급 요구와는 구조적으로 공존 불가**하다 (§7).

**언제 고르는가**: 이종 스택 조직·글로벌 기업·여러 리전/자회사 간 채팅 연합 필요·여러 런타임 엔진 혼용. "나중에 Phase 4까지 갈 가능성"을 열어두고 싶은 팀.

---

## 6. 핵심 공유 요소

3개 안은 다음 4대 공통 원칙 + 1개 공통 데이터 모델을 공유한다. 이는 R0 분석 단계에서 도출되어 세 차례 리뷰 사이클 동안 일관되게 유지되었다.

### 6.1 4대 공통 원칙

1. **서버는 경량 메시징 허브, 에이전트 두뇌는 엔진에 위임**. 서버는 라우팅·영속화·권한 검사·팬아웃만 담당하며, LLM 추론·도구 호출·사고 체인은 Claude Code SDK / Codex / OpenHands / Deep Agents 런타임에 위임한다. 서버는 엔진 내부 상태를 모델링하지 않는다.

2. **채팅은 네이티브 프로토콜, MCP는 외부 도구 영역**. 3개 안 모두 MCP(Model Context Protocol)를 채팅 메시지 수송에 사용하지 않는다. MCP는 에이전트가 외부 도구를 호출할 때만의 프로토콜이며, 채팅은 각 안이 선택한 네이티브 프로토콜을 쓴다.

3. **서브에이전트는 채널 기반 (`Room.parent_room_id`)**. 서브에이전트는 별도 엔티티나 MCP 래퍼가 아니라 부모 Room에 `parent_room_id` 관계를 가진 자식 Room이다. R1에서 안 A가, R2에서 안 C가 `parent_room_id + is_dm` 원자 속성으로 정규화를 완료했다.

4. **이종 엔진 혼합 가능**. 3개 안 모두 4개 런타임 엔진을 한 시스템에서 혼합할 수 있다. 차이는 혼합 방식이다 (안 A·B는 단일 프로토콜에 모두 붙고, 안 C는 엔진별로 다른 프로토콜을 선택할 수 있다).

### 6.2 7개 공통 엔티티

Project / Room / User / Agent / Machine / Participant / Message의 7개 엔티티가 3개 안 모두에서 동일한 기본 스키마를 가진다. 차이는 각 안의 확장 필드뿐이다. 특히 **Machine은 "에이전트 호스트"의 의미로 3개 안에 통일**되어 있으며, 안 C가 초안에서 "서버 인스턴스"로 혼동했던 것은 R2에서 정정되었다 (안 C §5.1).

### 6.3 SDK 어댑터 2단계 표기

`verified` (명시 SDK 버전에서 E2E 시나리오 통과) / `conceptual` (공식 문서 기반 설계 스케치) 2단계 표기가 3개 안 모두에 통일되어 있다. 현재 모든 엔진은 대부분 `conceptual` 상태이며, 각 안의 SDK 섹션에 승격 조건이 명시되어 있다.

---

## 7. 마이그레이션 경로

3개 안 간 마이그레이션은 가능하지만 비용과 리스크가 비대칭이다. 안 A를 출발점으로 삼은 조직이 가장 선택지가 많다.

| 경로 | LOC 변화 | 기간 | 리스크 | 주요 작업 |
|---|---|---|---|---|
| **A → C** | ~710 → ~1,120 (+410) | **2~3주** | 낮음 | WebSocket 어댑터는 호환 유지. `messages` 스키마 유지. `InProcessMessageBus` 도입 후 서비스 레이어가 `publish/subscribe`를 호출하도록 리팩터링. Phase 4 전환은 별도 단계 |
| **A → B** | ~710 → ~1,130 (+420) | **6~8주** | 중~높음 | `messages` → `events` 테이블 전면 변환 (`UPSERT` 금지). WebSocket → SSE+REST 전환 (클라이언트 재작성). DB 권한 분리 도입. 데이터 변환·read-only 윈도우·병렬 가동 필요. 안 A §13.3에 ~1,200~1,500 LOC 추가·기간 상세 |
| **C → A** | ~1,120 → ~710 (-410) | **1~2주** | 낮음 | 4개 어댑터 중 WebSocket만 남기고 나머지 제거. 이미 안 A 스키마와 호환되므로 DB 변환 불요 |
| **C → B** | **사실상 불가** | — | — | 다중 프로토콜·NATS 백본이 감사 1급 원칙과 구조적으로 충돌 (§7 공백 2). 감사 요구가 사후 발생하면 별도 안 B 인스턴스를 구축하는 편이 경제적 |
| **B → A** | ~1,130 → ~710 (-420) | 6~8주 | 높음 | Event Store 폐기는 감사 증거 능력 상실. 일반적으로 권장되지 않음 |
| **B → C** | — | — | — | 감사 요구가 없어진 경우에만 고려. 실무상 드문 경로 |
| **B ∥ C 공존** | 독립 배포 | 초기 2개 시스템 구축 | 중 | 감사 트래픽은 안 B, 이종 프로토콜 트래픽은 안 C. 두 시스템은 네트워크 격리되며 필요 시 Matrix federation으로 중계. 7개 엔티티 모델 호환으로 메시지 복제 도구를 공통화 가능 |

**권장 마이그레이션 전략**:
- 감사 요구가 **확정**이면 처음부터 안 B를 택하라. A→B 비용이 A→C의 2~3배이기 때문이다.
- 감사 요구가 **미확정**이고 다중 프로토콜이 예상되면 안 C Phase 1로 시작하라. Phase 4 전환 비용이 설정 한 줄이다.
- **감사 요구 + 다중 프로토콜** 조합이 필요하면 안 B ∥ 안 C 공존 전략이 유일한 해법이다.

---

## 8. 리더의 최종 판단

### 8.1 1순위 추천

**대부분의 조직에는 안 A 또는 안 C Phase 1로 시작할 것을 권장한다**. 이유:

- 감사 요구가 없다면 안 B의 Event Store와 DB 권한 분리는 운영 부담만 증가시킨다.
- 안 A와 안 C Phase 1의 LOC는 ~710 / ~800으로 차이가 작다 (~90 LOC). 안 C가 `MessageBus` Protocol 추상화를 가지고 있을 뿐이다.
- "Phase 4 전환 가능성"을 미래 옵션으로 유지하고 싶은 팀은 안 C Phase 1, 그렇지 않은 팀은 안 A가 최적이다.

### 8.2 성숙 단계별 전략

| 조직 단계 | 권장 전략 |
|---|---|
| **스타트업 초기 (팀 ≤10)** | 안 A 또는 안 C Phase 1. 규제 대응 여부로 결정 |
| **성장기 (팀 10~30)** | 안 C Phase 2~3 (필요 시 Phase 4 준비 시작) 또는 안 A + 세로 확장 |
| **성숙기 (팀 30+)** | 안 C Phase 4+ (이종 스택) / 안 B (규제 요구) |
| **엔터프라이즈** | 안 B (감사) + 필요 시 안 C 병행 |

### 8.3 주의 사항

1. **안 C Phase 5 Option B ≠ 안 B**. 선택적 Event Store는 안 B의 감사 1급을 대체하지 못한다. 안 B §16.2의 5가지 구조적 이유는 R2 리뷰에서 명시되었고 FINAL.md §7 "공백 시나리오"에서 재확인된다.

2. **"감사 1급 + 다중 프로토콜"은 구조적 충족 불가**. 한 시스템으로 이 조합을 시도하지 말 것. 요구 사항을 분리해 안 B ∥ 안 C 공존 전략을 택하거나, 다중 프로토콜을 포기하거나, 감사 요구를 재해석해야 한다.

3. **LOC 수치는 ±20% 범위의 설계 추정**이다. 구현 단계에서 변동 가능하며, FINAL.md 비교표의 수치를 계약 약속처럼 다루지 말 것.

4. **SDK 어댑터는 현 시점에서 대부분 `conceptual`이다**. 실제 통합 시점의 공식 SDK 문서를 기준으로 재검증 후 `verified`로 승격되어야 한다.

5. **구현 단계 실측 필수 항목**: 안 B의 LISTEN/NOTIFY SLA (인스턴스 수 / payload 크기 / PG 버전 의존), 안 C Phase 4의 드레인 창 (60/10/90초는 권장 기본값), 4개 SDK 어댑터의 E2E 시나리오.

6. **설계 사이클은 FINAL.md에서 종료되지만 운영 튜닝은 계속**된다. 3개 안 모두 production-ready 판정을 받았으나, "ready" 판정은 구현·운영 단계의 튜닝 책임을 면제하지 않는다.

### 8.4 구조적 공백 — "감사 + 다중 프로토콜" 불가 영역

본 설계 사이클이 명시적으로 다루지 못하는 영역이 하나 존재한다: **"감사 1급 + 다중 프로토콜 공존"**. 이 조합이 불가능한 구조적 이유는 5가지다 (안 B §16.2):

1. 이벤트 스키마 고정 vs 프로토콜별 메시지 형식 다양성 — 한 Event Store가 모든 프로토콜을 수용하려면 어느 한쪽이 데이터를 잃는다.
2. 순서 보장 — 단일 SSE는 `seq` 단조 증가가 자연스럽지만, 4종 프로토콜이 동시에 쓰면 `seq` 발급 경합이 감사 순서 신뢰성을 훼손한다.
3. 인증·권한 체계 파편화 — 프로토콜마다 인증 방식이 달라 감사 로그의 주체(identity) 일관성이 어렵다.
4. 재연결 시 중복/누락 검출이 프로토콜별로 다르면 감사 무결성 증명 비용이 N배가 된다.
5. payload hash 생성 시점이 프로토콜별로 다르면 단일 해시 체인이 불가능하다.

이 조합을 원하는 조직에게는 **요구 사항 분리**가 유일한 해법이다: 감사 대상 트래픽은 안 B, 다중 프로토콜 트래픽은 안 C, 두 시스템은 네트워크 격리. 7개 엔티티 공통 모델 덕분에 메시지 복제 도구는 공통 라이브러리로 작성 가능하다.

---

## 9. 결론

Doorae 채팅 서버 설계 사이클(R0→R1→R2→R3)은 3개 안으로 수렴했다. 3개 안은 서로 대체가 아닌 **서로 다른 조직 환경에 대응하는 독립적인 최적해**이며, 모두 Production-Ready 판정을 받았다.

- 감사 요구가 있으면 **안 B**
- 이종 프로토콜이 필요하면 **안 C**
- 그 외에는 **안 A** 또는 **안 C Phase 1**

R1 단계의 두 가지 치명적 이슈(안 B SSE 팬아웃, 안 C MessageBus 자기 모순)는 모두 해결되었고, R2의 크로스 일관성과 관측성 보강을 거쳐 3개 안은 나란히 비교될 수 있는 품질에 도달했다. R3에서 새로 발견된 설계 결함은 없다.

본 문서는 리더십이 실행 가능한 결정을 내릴 수 있도록 작성되었다. 구체적 구현 세부는 각 안의 제안서(`proposal-a-simple.md`, `proposal-b-production.md`, `proposal-c-flexible.md`)를 참조하라.

---

## 부록: 문서 맵

| 문서 | 경로 | 역할 |
|---|---|---|
| 구성 요소 매트릭스 | `episodes/final/00-component-matrix.md` | R0 9개 에피소드 × 10개 차원 분해 |
| 안 A 제안서 | `episodes/final/proposal-a-simple.md` | Simple/MVP 설계 (1,408줄) |
| 안 B 제안서 | `episodes/final/proposal-b-production.md` | Production/Enterprise 설계 (2,226줄) |
| 안 C 제안서 | `episodes/final/proposal-c-flexible.md` | Flexible/Multi-env 설계 (2,744줄) |
| R1 피드백 | `episodes/final/feedback-r1.md` | 1차 리뷰 (치명적 이슈 2건 발견) |
| R2 피드백 | `episodes/final/feedback-r2.md` | 2차 리뷰 (크로스 일관성·관측성 보강) |
| R3 피드백 | `episodes/final/feedback-r3.md` | 3차 리뷰 (production-ready 판정) |
| **본 문서 (FINAL)** | `episodes/final/FINAL.md` | 8개 섹션 통합 종합 보고서 |
