# 02. 근거 — 왜 이 스택을 골랐는가

> **한 줄 요약**: Python + FastAPI + SQLite + WebSocket + uvx는 "Docker 없음 + 쉬운 기동 + 이종 엔진 지원 + 경량"이라는 4개 요구를 동시에 만족하는 **유일한** 조합이다. Go/gRPC/PG/Docker를 선택하지 않은 이유를 정직하게 기록한다.

Plan A는 "무엇을 만들 것인가"를 정했다. 이 문서는 "어떤 도구로 만들 것인가"의 **근거**를 정리한다. 각 결정은 ADR(Architecture Decision Record) 6개로 최종 요약된다.

---

## 2.1 언어 선택 — 왜 Python인가

### 결정

**Python 3.11+** (3.12 권장, 3.13 호환).

### 후보군

| 후보 | 장점 | 단점 |
|---|---|---|
| **Python** | FastAPI 성숙도, 에이전트 SDK 생태계 Python 편향, 기존 Anygarden 코드 재사용, uvx 네이티브 | 단일 바이너리 크기 20-50MB, 시작 100-500ms, 메모리 50-100MB |
| **Go** | 단일 바이너리 10-20MB, 시작 <10ms, 메모리 5-15MB, 동시성 우수 | 기존 Anygarden Python 코드 폐기, 에이전트 SDK는 대부분 Python, 개발 속도 2-3배 느림 |
| **TypeScript (Node)** | 브라우저와 동일 언어, npm 생태계 | 서버 런타임 선택(Bun/Deno/Node) 분기, DB 드라이버 성숙도 Python에 못 미침 |
| **Rust** | 최고 성능, 작은 바이너리 | 개발 속도 매우 느림, 에이전트 SDK 부재 |

### 근거

1. **에이전트 엔진 SDK는 Python 편향이다**
   - Claude Code SDK: Python 1급 (TypeScript 2급)
   - Codex SDK: Python + TypeScript 동등
   - **OpenHands**: Python 전용 (TypeScript 없음)
   - **Deep Agents (LangGraph)**: Python 전용 (TypeScript 없음)

   4종 중 2종이 Python 전용이다. Go/TS 서버라면 **OpenHands/Deep Agents 사용자는 Python SDK를 따로 만들어야 하고**, 서버와 다른 언어 조합이 된다. 이 혼란을 피하려면 서버도 Python이 유리하다.

2. **FastAPI + uvicorn 생태계 성숙도**
   - WebSocket 내장 지원 (별도 라이브러리 불필요)
   - Pydantic v2 타입 안정성이 컴파일 시점 수준에 근접
   - `uvicorn[standard]` 단일 의존성으로 `websockets` 라이브러리까지 동봉
   - ASGI 표준으로 `starlette`/`httpx` 등과 상호운용

3. **개발 속도**
   - Python MVP: 2-3주
   - Go MVP: 4-6주 (테스트·문서·SDK 포함)
   - Anygarden는 MVP 속도가 **사용자가 가장 요구하는 것**이다. Plan A의 정체성이 "1-2주 MVP"인데 여기서 언어 교체는 모순이다.

4. **uvx 네이티브**
   - `uvx`는 PyPI 패키지를 일회성 실행하는 유일한 표준 툴이다.
   - Go는 `go install`이 있지만 이 경우 사용자에게 Go 툴체인을 요구한다.
   - Python + `uv`는 "사용자가 `uvx anygarden-server`만 알면 된다" 수준의 UX를 제공한다.

5. **기존 Anygarden 코드 재사용**
   - 이 리포지토리에는 이미 `MeetingEngine`, `AgentProfile` 등 Python 구현이 존재한다.
   - 이것들을 서버에 통합하거나 참조하려면 Python이 자연스럽다.

### Go를 선택하지 않은 이유 (정직한 고백)

**Go의 장점이 더 크지 않은가?**

- 단일 10MB 바이너리, 메모리 5-15MB, 시작 <10ms — 이 숫자들은 매력적이다.
- "경량성 요구"를 가장 직접적으로 만족하는 언어는 Go다.

**그러나**:

- Plan A는 "LOC ~800-950"이 목표다. 이 규모에서 Go/Python의 성능 차이는 **사용자가 체감하지 못한다**. 병목은 LLM 응답(1-10초)이며, 서버 내부 지연(수십 ms) 차이는 무의미하다.
- 50 동시 접속 · 10 msg/s 워크로드에서 Python도 충분히 여유가 있다 (CPU 사용률 <20% 예상).
- Python을 쓰면 **개발 속도 2배 + 생태계 호환 100%**를 얻는다. Go를 쓰면 **성능 여유 10배 + 호환성 50%**가 된다. 전자가 이긴다.

**Go rewrite의 시점**: 만약 "진짜로 10MB 단일 바이너리가 필수이고, Python 런타임 50MB도 용납 불가"라는 요구가 후속으로 들어오면, 그때 Go 포트를 고려한다. 이 구현의 프로토콜은 언어 독립적이므로 Go 서버와 Python SDK가 동일한 WebSocket 프레임으로 통신할 수 있다 — 마이그레이션 비용은 ~4-6주의 서버 rewrite일 뿐, SDK와 프로토콜은 건드릴 필요 없다.

---

## 2.2 DB 선택 — 왜 SQLite 기본 + PostgreSQL 옵션인가

### 결정

**SQLite를 기본**으로 쓰고, 4개 전환 조건 중 하나라도 해당되면 **PostgreSQL로 승격**한다.

### 후보군

| DB | 장점 | 단점 |
|---|---|---|
| **SQLite** | 제로 설정, 임베디드, 단일 파일, 백업 `cp` 한 줄 | 쓰기 직렬화 (writer 1), 수평 확장 불가 |
| **PostgreSQL** | 산업 표준, MVCC, PITR, 확장성 | 별도 프로세스 운영, 초기 설정 필요, "Docker 없음" 요구와 마찰 |
| **MySQL/MariaDB** | 친숙 | Python async 드라이버 성숙도 낮음, 이점 없음 |
| **임베디드 KV (RocksDB/LMDB)** | 빠름 | 쿼리 언어 없음, SQL 코드와 비호환 |

### 근거

1. **"Docker 없음" 요구와의 정합성**
   - PostgreSQL을 기본으로 하면 사용자가 **서버 프로세스 + PG 프로세스**를 동시에 관리해야 한다.
   - Docker 없이 이 조합을 기동하려면 `brew install postgresql && brew services start postgresql` 같은 단계가 필요하다 — 이미 5분 UX를 깨뜨린다.
   - SQLite는 `uvx anygarden-server` 한 줄로 완결된다. DB 프로세스가 별도로 존재하지 않는다.

2. **MVP 워크로드 적합성**
   - Plan A 목표: 50 접속 · 10 msg/s · DB <2GB.
   - SQLite WAL 모드는 이 워크로드의 10배까지 여유있게 처리한다.
   - 참조 벤치마크(WAL, SSD, 공개 측정치 기준): 대략 ~5,000 write/s · 100MB DB에서 p99 <5ms 수준이 보고된다. 구현 시점에 실측으로 재확인 필요.

3. **관측·백업 단순성**
   - `cp ~/.anygarden/anygarden.db backup-20260406.db` — 백업 끝.
   - `sqlite3 anygarden.db ".dump" > backup.sql` — 평문 덤프.
   - 규제 환경이 아닌 한, 이 수준의 백업이면 충분하다.

4. **PG 승격 경로가 열려 있다**
   - SQLAlchemy가 SQLite/PG 양쪽을 지원한다.
   - Plan A §4.3에 명시된 4가지 전환 조건 (DB >2GB, 쓰기 >10 msg/s, 동시 접속 >100, PITR 요구) 중 하나라도 해당되면 승격한다.
   - 승격 비용: ~40-60줄 코드 변경 (advisory lock) + Alembic + 데이터 덤프 이동 1-2일.

### SQLite/PG 차이 (재요약)

| 측면 | SQLite | PostgreSQL | 코드 영향 |
|---|---|---|---|
| 드라이버 | `aiosqlite` | `asyncpg` | 의존성만 |
| URL | `sqlite+aiosqlite:///~/.anygarden/anygarden.db` | `postgresql+asyncpg://...` | `.toml` |
| `seq` 발급 | WAL writer 직렬화 (경합 없음) | advisory lock 필요 | `repository.py` ~40줄 분기 |
| 백업 | `cp` or `.backup` | `pg_dump` + PITR | 운영 절차 |

---

## 2.3 프로토콜 선택 — 왜 WebSocket 단일인가

### 결정

**WebSocket 단일 프로토콜** (JSON 텍스트 프레임만).

### 후보군

| 프로토콜 | 장점 | 단점 |
|---|---|---|
| **WebSocket** | 브라우저 네이티브, 양방향, 저지연, DevTools로 직접 관찰 | 프록시/LB 호환성 이슈 (스티키 세션) |
| **SSE + REST POST** | HTTP 표준, curl로 디버깅, Last-Event-ID 표준 | 단방향 (POST 왕복 필요), 브라우저 동시 연결 제한 |
| **gRPC bidi** | 타입 안정성, 성능 | 브라우저 비호환 (gRPC-web 스택 필수), 바이너리 디버깅 어려움 |
| **NATS 직접** | 브로커가 복제·재연결·팬아웃 처리 | 브라우저 비호환, NATS 서버 별도 기동 |
| **graphql-ws** | 스키마 통합 | Apollo 스택 강요, 오버엔지니어링 |
| **4종 하이브리드 (Plan C)** | 최대 유연성 | 유지비용 4배, 테스트 매트릭스 폭발 |

### 근거

1. **브라우저 네이티브성**
   - `new WebSocket("wss://...")` 한 줄로 연결된다. 클라이언트 라이브러리가 필요 없다.
   - 이것이 "Docker 없음 + 쉬운 기동" 요구의 **클라이언트 측 대응**이다.

2. **MCP를 채팅 프로토콜로 쓰지 않는 이유**
   - MCP는 도구 호출 프로토콜이다. 채팅 메시지를 `tool_result`로 포장하면 한 메시지당 500-1000 토큰이 소모된다.
   - WebSocket 네이티브 프레임은 ~20 토큰/msg 수준이다.
   - **토큰 효율 10-30배 차이**. 하루 1,000 메시지 × 4 에이전트 × 30일 = 월 4-10만 달러의 LLM 비용 차이가 될 수 있다.
   - MCP는 **외부 도구 호출 경로**로만 쓴다. 자세한 내용은 [06-mcp-integration.md](06-mcp-integration.md) 참조.

3. **관측성**
   - 브라우저 DevTools의 Network → WS → Messages 탭으로 모든 프레임이 JSON 텍스트로 보인다.
   - gRPC Protobuf나 NATS 바이너리는 이 수준의 관측성을 제공하지 못한다.
   - `websocat ws://localhost:8000/ws/rooms/xxx --protocol 'anygarden.v1,bearer.<token>'` 한 줄로 수동 테스트 가능. 토큰은 `Sec-WebSocket-Protocol` 헤더로 전달하며 URL 쿼리에는 넣지 않는다(05-security.md §5.7.1).

4. **단일 연결 다중 Room 멀티플렉싱**
   - 한 WebSocket 연결로 여러 Room에 구독할 수 있다 (`join_room` 프레임으로 확장).
   - SSE는 Room마다 연결을 따로 맺거나 브라우저 동시 연결 제한(6)을 감당해야 한다.

5. **SSE+REST 하이브리드를 쓰지 않는 이유**
   - SSE는 단방향이므로 "유저가 메시지를 보낸다"에 REST POST가 별도로 필요하다.
   - 결국 "SSE 연결 + POST 요청 짝"을 관리해야 하며, 이는 WebSocket보다 **덜** 단순하지 않다.
   - curl 디버깅 이점은 있지만 WebSocket DevTools가 같은 관측성을 제공한다.

---

## 2.4 SDK 전략 — 왜 Python 1급 + TypeScript 2급인가

### 결정

- **Phase 1 (MVP)**: Python SDK (`anygarden-sdk` on PyPI) **단독**
- **Phase 2**: TypeScript SDK (`@anygarden/sdk` on npm) 추가

### 근거

1. **에이전트 엔진의 언어 분포**

   | 엔진 | Python | TypeScript |
   |---|---|---|
   | Claude Code SDK | 1급 | 1급 |
   | Codex SDK | 1급 | 1급 |
   | OpenHands | **1급 전용** | 없음 |
   | Deep Agents (LangGraph) | **1급 전용** | 없음 |

   **4종 중 2종이 Python 전용**이다. Python SDK가 없으면 OpenHands/Deep Agents 사용자는 커스텀 통합을 직접 작성해야 한다. 이는 Plan A가 약속한 "이종 엔진 혼용 가능"을 깬다.

2. **TypeScript SDK의 필요성 (Phase 2)**
   - Codex와 Claude Code의 많은 사용자가 TypeScript를 선호한다.
   - Node.js 환경에서 에이전트를 돌리는 팀이 적지 않다.
   - Phase 1 이후 커뮤니티 요구가 커지면 추가한다.

3. **서버와 SDK는 독립적이다** (R3 피드백 반영)
   - "서버가 Python이니 SDK도 Python만 있어야 한다"는 잘못된 가정이다.
   - 서버와 SDK는 **WebSocket 와이어 프로토콜만 공유**한다. 내부 구현 언어는 서로 무관하다.
   - 장기적으로 Go 서버 + Python/TS SDK 조합도 가능하다 (프로토콜 안정성이 보장되는 한).

4. **두 SDK가 공유하는 것과 아닌 것**

   | 항목 | 공유 | 비공유 |
   |---|---|---|
   | WebSocket 프레임 JSON 스키마 | ✓ | |
   | 재연결 로직 (Last-Seq) | ✓ (개념) | 구현 언어별 |
   | 엔진 통합 (Python 쪽만 OpenHands/DeepAgents) | | ✓ |
   | 엔진 통합 (양쪽 모두 Claude Code, Codex) | ✓ (개념) | 구현 언어별 |

---

## 2.5 배포 전략 — 왜 uvx + 선택적 PyInstaller인가

### 결정

- **1차 배포 수단**: `uvx anygarden-server` (PyPI 패키지)
- **2차 배포 수단**: PyInstaller 단일 바이너리 (Python 미설치 환경용)
- **Docker 이미지**: 제공하지 않음 (사용자 요구에 반함)
- **K8s manifest**: 제공하지 않음 (MVP 범위 밖)

### 후보군 비교

| 배포 수단 | 사용자 준비물 | 기동 명령 | 크기 | 첫 기동 시간 |
|---|---|---|---|---|
| `uvx` | `uv` 설치 (`curl -Ls https://astral.sh/uv/install.sh | sh`) | `uvx anygarden-server` | 패키지 다운로드 ~10MB | ~10초 (첫 실행) / <1초 (캐시) |
| `pip install + script` | Python + pip | `pip install anygarden-server && anygarden-server` | ~10MB | ~30초 |
| `pipx` | pipx 설치 | `pipx install anygarden-server` | ~10MB | ~30초 |
| PyInstaller 바이너리 | 없음 | `./anygarden-server` | 20-50MB | <1초 |
| Docker | Docker | `docker run anygarden/server` | ~100MB 이미지 | ~5초 |

### 근거

1. **`uvx`가 이 요구사항의 이상적 해답이다**
   - 단일 명령으로 "패키지 다운로드 → 임시 venv → 실행"이 끝난다.
   - 사용자는 Python 버전 관리나 venv 생성을 신경 쓸 필요가 없다.
   - `uv`는 Rust로 작성된 빠른 패키지 관리자로 업계에서 사실상 표준이 되고 있다.
   - **"Docker 없음 + 쉬운 기동"에 가장 가까운 단일 수단**이다.

2. **PyInstaller는 백업 수단**
   - Python이 아예 설치되지 않은 환경 (예: 임베디드 서버, 일부 보안 제약 환경)
   - 비개발자에게 "이 바이너리 하나 받아서 실행하세요"로 배포할 때
   - 크기 20-50MB는 Go 바이너리(10-20MB)보다 크지만, **첫 실행이 1초**인 이점이 있다.

3. **Docker를 배제하는 이유**
   - 사용자 요구사항 1번: "Docker 사용 안 함"
   - Docker는 이 MVP에 불필요한 레이어다. 서버가 단일 Python 프로세스인데 컨테이너화할 이유가 없다.
   - 프로덕션에서 컨테이너가 필요한 팀은 스스로 `Dockerfile`을 2줄로 작성하면 된다 — 공식 지원은 하지 않는다.

4. **systemd user unit**을 권장
   - 상시 실행이 필요하면 systemd user unit으로 관리한다 (sudo 불필요, 홈 디렉토리에서 완결).
   - 예시는 [08-operations.md](08-operations.md) §8.4.2 참조.

---

## 2.6 Plan B·Plan C와의 차이 재확인

이 구현은 Plan A를 따르며, B/C를 선택하지 않는다. 각 결정을 이 구현 관점에서 재확인한다.

### Plan B를 선택하지 않는 이유

| Plan B 결정 | 이 구현의 선택 | 비용 차이 |
|---|---|---|
| Event Store (append-only immutable) | 일반 `messages` 테이블 + seq | Plan B 대비 -400 LOC |
| SSE + REST POST | WebSocket | 디버깅 UX 비슷, WS가 더 단순 |
| PostgreSQL `LISTEN/NOTIFY` 팬아웃 | 인메모리 ConnectionManager | Plan B 대비 -180 LOC |
| 권한 분리 DB 계정 (reader/writer/admin) | 단일 계정 | 운영 복잡도 ↓ |

**언제 Plan B로 가는가**: SOX/MiFID II/HIPAA/SOC 2 규제 감사 요구 발생 시. 그때는 Event Store를 도입하고 권한 분리를 추가해야 한다 (Plan A proposal §13.3의 마이그레이션 경로 참조).

### Plan C를 선택하지 않는 이유

| Plan C 결정 | 이 구현의 선택 | 비용 차이 |
|---|---|---|
| 4종 프로토콜 (WS+SSE+gRPC+NATS) | WebSocket 단일 | Plan C 대비 -300 LOC + 테스트 매트릭스 1/4 |
| NATS 백본 | 인메모리 팬아웃 | 외부 브로커 불필요 |
| 다중 인스턴스 수평 확장 | 단일 프로세스 | 운영 복잡도 극단적으로 낮음 |

**언제 Plan C로 가는가**: 동시 접속 수백~수천, 수평 확장 필수, 여러 팀이 서로 다른 프로토콜을 선호 시. 그때는 `MessageBus` 추상화를 도입하고 NATS를 추가한다 (Plan A proposal §13.2 참조).

---

## 2.7 ADR (Architecture Decision Records)

최종 결정을 6개 ADR로 요약한다. 각 ADR은 후속 결정에서 참조할 수 있는 **불변 기록**이다.

### ADR-001: Python 서버 선택

- **상태**: Accepted (2026-04-06)
- **컨텍스트**: Anygarden 멀티 에이전트 채팅 서버 구현 언어 결정
- **결정**: Python 3.11+ with FastAPI
- **결과**:
  - 에이전트 엔진 SDK 생태계와 자연스러운 통합
  - 개발 속도 최적화 (2-3주 MVP)
  - 바이너리 크기 20-50MB 수용 (Go 대비 크지만 허용 범위)
- **대안 기각**: Go (생태계 단절), TS (OpenHands/DeepAgents 부재), Rust (속도 부족)

### ADR-002: WebSocket 단일 프로토콜

- **상태**: Accepted (2026-04-06)
- **컨텍스트**: 채팅 프로토콜 선택
- **결정**: WebSocket JSON 텍스트 프레임 단일 사용
- **결과**:
  - 브라우저 네이티브 + 토큰 효율 + DevTools 관찰성
  - MCP는 외부 도구 영역으로 완전 분리
- **대안 기각**: SSE+REST (양방향 불편), gRPC (브라우저 비호환), 4종 하이브리드 (Plan C 오버헤드)

### ADR-003: SQLite 기본 + PostgreSQL 승격 옵션

- **상태**: Accepted (2026-04-06)
- **컨텍스트**: 데이터베이스 선택
- **결정**: SQLite가 기본, 4개 조건 중 하나라도 충족 시 PG 승격
- **결과**:
  - `uvx anygarden-server` 한 줄 기동 가능
  - PG 승격 비용 ~40-60 LOC + Alembic
- **조건**: DB >2GB OR 쓰기 >10 msg/s OR 동시 접속 >100 OR PITR 요구

### ADR-004: Python SDK 1급 / TypeScript SDK 2급

- **상태**: Accepted (2026-04-06)
- **컨텍스트**: 에이전트 SDK 언어 전략
- **결정**: Phase 1은 Python SDK만, Phase 2에 TypeScript SDK 추가
- **결과**:
  - 4종 엔진 모두 Phase 1에서 지원 가능 (OpenHands/DeepAgents가 Python 전용이므로)
  - TS 사용자는 Phase 2까지 대기 또는 Python 브리지 사용
- **대안 기각**: TS 우선 (OpenHands/DeepAgents 부재), 양쪽 동시 (리소스 부족)

### ADR-005: uvx 1차 배포 + PyInstaller 2차 배포

- **상태**: Accepted (2026-04-06)
- **컨텍스트**: 배포 방법 결정
- **결정**: PyPI 패키지 + `uvx` 실행을 주력, PyInstaller 바이너리는 선택
- **결과**:
  - 5분 내 기동 가능 (uvx)
  - Python 미설치 환경 대응 (PyInstaller)
  - Docker 미제공 (사용자 요구사항 준수)
- **대안 기각**: Docker 기본 (요구사항 위반), K8s manifest (MVP 범위 밖)

### ADR-006: Machine을 1급 스케줄링 리소스로

- **상태**: Accepted (2026-04-08)
- **컨텍스트**: 초안 impl/에서 Machine을 수동 메타데이터로만 취급하여, 사용자가 매번 `uvx anygarden-agent`를 수동 실행하는 전제였음. 그러나 원래 요구사항("프로젝트·머신·에이전트·유저" 4대 엔티티 + "호스트당 복수 에이전트")은 **Machine을 활성 참여자**로 다룰 것을 암시함. 수동 운영은 1-3 머신 로컬 테스트에는 맞지만, "알리스는 노트북에 2개 + 밥은 GPU 머신에 3개" 같은 분산 배치가 불가능.
- **결정**: Machine은 `anygarden-machine` Daemon을 통해 서버에 등록되는 **1급 스케줄링 리소스**. 서버는 Machine을 대상으로 "이 엔진으로 에이전트 띄워라"를 선언적으로 명령하고, Daemon이 로컬 subprocess를 관리한다. Daemon 없이 수동 실행하는 **standalone 모드도 폴백으로 유지**한다.
- **결과**:
  - Machine 등록 후 웹/API로 선언적 에이전트 생성 가능
  - 다중 호스트 운영 가능 (최대 수십 대까지 bin-pack)
  - Agent 크래시 자동 복구 (restart_policy 설정 가능)
  - 엔진 감지 자동화 (detector.py가 claude-code/codex/openhands/deep-agents/openai/anthropic 검사)
  - 서버 본체 LOC: §1-§9 기준선 ~960 (01 §1.3 확정) → §10 포함 ~1,330-1,750 (Machine 스케줄러 추가; 01 §1.10.4)
  - 3번째 패키지 `anygarden-machine` 추가 (~410 LOC)
  - MVP 기간 2-3주 → 4-5주
- **대안 기각**:
  1. **Standalone만 유지 (초안)**: 수동 운영 전제. 원래 요구사항 미충족
  2. **Plan B의 OS 프로세스 격리 채택**: systemd/Docker/K8s manifest 강제. "Docker 없음" 요구 위배
  3. **Machine Daemon 패턴** (채택): 중간 지점. Daemon은 경량 Python 프로세스 + systemd user unit만 필요. Docker 불필요
- **완화책**:
  - Standalone 모드(수동 `uvx anygarden-agent`)를 그대로 유지하여 소규모/로컬 테스트 경로 보존
  - 서버 본체 복잡도 증가는 명확한 모듈 분리 (`anygarden/scheduler/`)로 국지화
  - 토큰 3종 분리(User/Agent/Machine): 각 주체의 **직접 권한**을 분리하고 운영 혼동을 막는다. 단, Daemon이 침해되면 spawn 경로를 통해 Agent Token이 평문으로 노출되므로 이 분리만으로 Daemon 침해 하 메시지 보호는 성립하지 않는다. 상세는 [05-security.md](05-security.md) §5.11.2.1 참조

자세한 설계는 [10-machine-scheduler.md](10-machine-scheduler.md) 참조.

---

## 2.8 한 문장 정리

> 이 구현은 "**가장 많은 에이전트 엔진을 가장 적은 코드로 가장 쉽게 기동하되, 머신을 1급 리소스로 관리**"라는 목표의 교집합에서 유일하게 실행 가능한 해답이다. Python이 아니면 OpenHands/DeepAgents를 잃고, WebSocket이 아니면 토큰 효율을 잃고, SQLite가 아니면 "한 줄 기동"을 잃고, Machine Daemon이 아니면 다중 호스트 운영을 잃는다.
