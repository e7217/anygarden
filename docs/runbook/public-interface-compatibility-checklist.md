# 공개 인터페이스 버전별 호환성 체크리스트 (ANY-3)

## 버전 맥락

- `anygarden` (cluster): `0.18.0`
- `anygarden-agent`: `0.12.0`
- `anygarden-machine`: `0.14.1`

## 변경 대상 공개 인터페이스

### 1) REST API 오류 바디

- 경로: `anygarden` API의 **선택된** `GET/POST/PATCH/PUT/DELETE /api/v1/machines`, `/api/v1/tasks` 오류
- 개선: 기존 FastAPI `detail` 값과 타입은 그대로 두고 top-level `code`/`message`를 추가

| 인터페이스 | 변경 전 | 변경 후 | 호환성/버저닝 판단 |
|---|---|---|---|
| 선택된 machine/task 문자열 오류 | `{"detail": "..."}` | 기존 `detail` 문자열 + top-level `code`/`message` | 기존 `body.detail` 값·타입을 보존하는 additive 변경 |
| machine 삭제 409 | `detail.error=machine_has_active_agents` | 기존 `detail` 객체 + top-level `code=MACHINE_HAS_ACTIVE_AGENTS`/`message` | 기존 객체를 그대로 보존하는 additive 변경 |
| 기존 task 코드형 오류 | `detail.code` + 엔드포인트별 부가 필드 | 기존 형식 유지 | 이번 변경에서 제거/이름 변경 없음 |
| 그 외 API 오류 | endpoint별 FastAPI/커스텀 오류 | 기존 형식 유지 | ANY-3 공통 schema 적용 대상이 아님 |

새 registry에 포함된 오류만 top-level `code`를 분기 기준으로 사용할 수 있습니다. `message`는 표시용입니다. 기존 v1 소비자가 읽는 `detail` 문자열/객체는 그대로 유지됩니다. 응답 전체는 FastAPI wrapper이므로 예를 들어 `MACHINE_OFFLINE`은 `{"detail":"Machine is not connected","code":"MACHINE_OFFLINE","message":"Machine is not connected"}`입니다.

Registry는 `packages/cluster/anygarden/api/v1/errors.py`의 `PUBLIC_ERROR_CODES`와 `packages/cluster/docs/api.md`의 표가 기준입니다. v1 안에서는 code 제거/이름 변경과 legacy `detail` 타입 변경을 금지합니다. 새 code와 top-level context field만 additive로 추가할 수 있습니다.

### 2) CLI 사용성

- 대상: `anygarden`, `anygarden-agent`, `anygarden-client`
- 문서: 지원 engine과 agent 필수 `--engine/--name/--server/--room`,
  machine `run` passthrough를 실제 help와 일치
- 동작 수정: `anygarden server --config PATH`가 실제 `.env`를 로드하며,
  미지정 시 `server init`이 만든 `~/.anygarden/config.env`를 자동 사용
- 우선순위: 명시적 CLI 옵션 > 프로세스 `ANYGARDEN_*` 환경변수 > `.env` > 기본값

## 릴리스별 호환성 계획

| 릴리스 | 요구 사항 |
|---|---|
| 현재 (`anygarden` 0.18.x) | 기존 `detail`을 유지하고 registry 대상에 top-level `code`/`message`만 추가; `machine_has_active_agents`의 `detail.error`도 유지 |
| 다음 minor | 새 code/context는 additive로만 추가하고 릴리스 노트에 registry 차이를 게시 |
| 새 API version/다음 major 후보 | 사전 폐기 공지와 소비자 점검 후에만 `detail` 타입 변경, code rename/removal, legacy `detail.error` 제거 검토 |

## 수동 점검 항목 (수동 릴리스 전)

- [x] `MACHINE_HAS_ACTIVE_AGENTS`의 기존 object `detail` snapshot 및 top-level code 확인 (`test_api_errors.py`, `test_machines_api.py`)
- [x] 변경된 machine/task 13개 code의 기존 `detail` + 새 envelope snapshot 확인 (`test_api_errors.py`)
- [x] 실제 machine/task 대표 호출의 FastAPI wrapper 응답 확인 (`test_machines_api.py`, `test_tasks_api.py`)
- [x] `PUBLIC_ERROR_CODES`와 machine/task helper 호출 및 snapshot 목록의 집합 일치 확인 (`test_api_errors.py`)
- [x] `anygarden-agent --help`의 engine 목록과 README 명령의 `--engine` 값이 실제 `ENGINES` 안에 있는지 확인 (`test_cli.py`)
- [x] 저장소 공개 SDK/CLI에서 machine/task REST `detail`/`error` 분기 의존 없음
  (`rg` 확인; agent의 `detail` 사용은 WebSocket server-error 로깅으로 별도 계약)
- [x] 알 수 없는 외부 소비자는 테스트로 고정된 기존 `detail` 값/타입을 그대로
  받으므로 0.18.x에서 마이그레이션 없이 동작
- [x] unified server/machine/agent/client `--help`, 명시/default config 및
  CLI override 우선순위 검증 (`test_cli_usability.py`)
- [x] `packages/cluster/CHANGELOG.md` Unreleased에 additive 계약과 CLI 수정 기록
