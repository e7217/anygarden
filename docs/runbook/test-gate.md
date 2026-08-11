# 테스트 게이트 가이드 (ANY-6)

## 1) 테스트 카탈로그

### 1-1. CI 기본 게이트 레이어

- **Unit / 빠른 단위**
  - 대상: 각 패키지의 경량 단위 테스트
  - 진입점
    - `packages/machine/tests`
    - `packages/agent/tests`
    - `packages/cluster/tests`
  - CI 실행: `.github/workflows/ci.yml`의 `test-linux`
  - 게이트: 실패 0건
  - 임계치: `-p no:libtmux` + Windows 예외 케이스 제외(아래 참조)

- **Integration / 컴포넌트 통합**
  - 대상: API 라우트, 스케줄러, 워커/매니저 연동 테스트
  - 진입점
    - `packages/cluster/tests` 전반 (`routes`, `lifecycle`, `repository`, `ws`, `llm_gateway`, `rooms`)
    - `test-agent-ts`의 Node 계열 타입체크/빌드 전환 테스트
  - CI 실행: `test-linux`
    - `.github/workflows/ci.yml` Job: `test-agent-ts`
    - `@anygarden/agent-ts` 타입체크/테스트/빌드
  - 게이트: 실패 0건

- **E2E / 사용자 플로우**
  - 대상: 대화 세션, 룸 생애주기, 엔진 장애/복구, 방어적 장애 검증
  - 진입점
    - `packages/cluster/tests/test_e2e_scenario.py`
    - `packages/cluster/tests/test_e2e_materialize.py`
    - `packages/cluster/tests/test_e2e_real_conversation.py` (`pytest -m slow`)
    - `packages/cluster/scripts/e2e_full_pipeline.py` (수동 실행 스크립트)
    - `packages/cluster/tests/test_e2e_gate_catalog.py` (**신규: ANY-6 산출물**)
  - CI 실행
    - `test-linux`의 클러스터 테스트 전체 (`pytest -x`)
    - E2E 스몰: `test_e2e_scenario.py`, `test_e2e_materialize.py`
    - 스로우: `test_e2e_real_conversation.py`는 기본 게이트 제외(로컬/수동)
  - 게이트: 실패 0건

- **Frontend E2E / UI 계약**
  - 대상: `packages/cluster/frontend` Playwright + Vitest
  - CI 실행: `.github/workflows/ci.yml` Job: `build-frontend`
  - 게이트: 실패 0건
  - 아티팩트: `packages/cluster/frontend/test-results/`

- **Engine Smoke (릴리스 게이트 전용)**
  - 대상: `packages/cluster/scripts/engine_smoke_gate.py`
  - CI 실행: `.github/workflows/engine-smoke.yml`, 릴리스 경로에서 선행 요구
  - 게이트: `result_code == PASS` 또는 `PREFLIGHT_PASS` + 실행 컨텍스트 일치

### 1-2. Windows 예외

- `test-windows`는 인증/권한/파일시스템 동작만 선별 실행:
  - `tests/test_safefs_win.py`
  - `tests/test_proc_kill.py`

## 2) 신규 E2E 시나리오 (문서/코드)

- **에이전트 생성 + 룸 온보딩 + 세션 복구**
  - 파일: `packages/cluster/tests/test_e2e_gate_catalog.py`
  - 절차: API로 에이전트 생성 → 룸 참가자 등록 → 유저/에이전트 WS 연결 → disconnect/reconnect with `since_seq`
  - 기대: 인증된 토큰으로 연결 가능, 세션 재연결 시 누락 메시지 재수신

- **룸 라이프사이클**
  - 파일: `packages/cluster/tests/test_e2e_gate_catalog.py`
  - 절차: 룸 생성 → 이름 변경 → Archive → Unarchive → 삭제
  - 기대: 각 API 단계별 상태 전이 정합성

- **엔진 장애 대응**
  - 파일: `packages/cluster/tests/test_e2e_gate_catalog.py`
  - 절차: 미지원 엔진 에이전트 생성 → `unavailable_reason == no_machine_for_engine` 확인 → 머신 지원 엔진 추가 → `/api/v1/agents/{id}/start` 후 장애 이유 제거
  - 기대: 장애 원인 식별 및 복구 가능성 확인

## 3) 실행 로그 템플릿 (문서/실패 대응 공통)

```json
{
  "pipeline": "ci",
  "run_id": "2026-08-06T12:00:00Z",
  "branch": "main",
  "sha": "<GIT_SHA>",
  "layer": "e2e|integration|unit|frontend|smoke",
  "job": "test-linux|test-agent-ts|build-frontend|engine-smoke",
  "suite": "packages/cluster/tests/test_e2e_gate_catalog.py",
  "result": "pass|fail|blocked",
  "duration_ms": 0,
  "total": 0,
  "passed": 0,
  "failed": 0,
  "xfailed": 0,
  "xfail": 0,
  "retries": 0,
  "artifacts": [
    {
      "type": "log|junit|screenshot",
      "path": "<artifact_path>"
    }
  ],
  "failures": [
    {
      "test_id": "packages/cluster/tests/test_e2e_gate_catalog.py::...",
      "error_category": "contract|infra|flaky|smoke_runtime",
      "failure_code": "NO_MACHINE_FOR_ENGINE|PREFLIGHT_PANIC|TIMEOUT",
      "first_seen": "<ISO8601>",
      "impact": "P0|P1|P2",
      "owner": "qa|infra|backend",
      "next_action": "rerun|rollback|investigate|patch"
    }
  ]
}
```

## 4) 임계치 및 차단 조건

- **공통 임계치**: 필수 레이어(`test-linux`, `build-frontend`, `test-agent-ts`) 실패 시 게이트 차단
- **수동 실행 스로우**: 실패 시 `skip` 처리하지 않으며 이슈 주석과 증적 보강 필수
- **Release smoke**:
  - `engine_smoke_gate`의 hard fail 조건
    - `HARD_TIMEOUT_SECONDS = 60`
    - `MAX_RESPONSE_BYTES = 256`
    - `MAX_RESPONSE_EVENT_BYTES = 65536`
    - `MAX_FAILURE_MESSAGE_BYTES = 4096`
  - 실패 코드: `FAIL_CANARY_MISMATCH`, `FAIL_ENGINE_NONZERO`, `FAIL_PROTOCOL_OUTPUT_LIMIT`,
    `FAIL_TOOL_REQUESTED`, `FAIL_RESPONSE_LIMIT`, `FAIL_PROTOCOL_SHAPE`, `FAIL_APPROVAL_REQUESTED`
  - 환경검증 실패: `BLOCKED_CONFIGURATION`
- **회귀 경고 레벨(권장)**:
  - P0: `preflight` 실패 또는 smoke 게이트 실패(릴리스 파이프라인 블로킹)
  - P1: 필수 E2E에서 1건 실패(릴리스 차단권장)
  - P2: 비필수/재현성 낮은 실패(재실행 후 재확인)

## 5) 실패 보고 템플릿

- 제목: `[QA-E2E] <날짜> <파이프라인> <레이어> <간단 요약>`
- 본문 필드:
  1. **요약**: 실패 증상, 영향 범위
  2. **재현 절차**: 실패한 테스트명과 정확한 CLI/요청
  3. **증적 경로**: 로그/아티팩트/스크린샷
  4. **증상 분류**
     - `infra`: 환경/네트워크/권한
     - `contract`: API/스키마/권한
     - `smoke_runtime`: smoke 런타임 제한(`HARD_TIMEOUT_SECONDS`, `MAX_RESPONSE_BYTES`)
  5. **임시 대응**: 롤백 대상, 스킵/보류 여부, 재시도 횟수
  6. **근본 원인 후보** 및 **수정 항목**
  7. **완료 기준**: 같은 환경에서 재실행 PASS 또는 차선책 합의

## 6) 현재 상태

- 테스트 카탈로그와 실행 기준은 이 문서 기준으로 운영.
- 신규 E2E 케이스는 `test_e2e_gate_catalog.py`로 정리되어 있으며,
  에이전트 생성/룸 라이프사이클/엔진 장애 대응 플로우를 포함.
