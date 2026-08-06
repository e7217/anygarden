# 공개 인터페이스 버전별 호환성 체크리스트 (ANY-3)

## 버전 맥락

- `anygarden` (cluster): `0.18.0`
- `anygarden-agent`: `0.12.0`
- `anygarden-machine`: `0.14.1`

## 변경 대상 공개 인터페이스

### 1) REST API 오류 바디

- 경로: `anygarden` API `GET/POST/PATCH/PUT/DELETE /api/v1/machines`, `/api/v1/tasks`
- 개선: 오류 바디를 `code` 중심으로 정규화

| 인터페이스 | 변경 전 | 변경 후 | 호환성/버저닝 판단 |
|---|---|---|---|
| 선택된 machine/task 오류 | `detail` 문자열 또는 엔드포인트별 객체 | `detail.code`, `detail.message`, `detail.detail` | 오류 문자열을 직접 비교하는 클라이언트에는 호환되지 않음. 다음 `anygarden` minor release notes에 명시하고, 한 minor 동안 아래 레거시 필드를 유지 |
| machine 삭제 409 | `detail.error=machine_has_active_agents` | 기존 `error` + 새 `code=MACHINE_HAS_ACTIVE_AGENTS` | 추가 필드만 생기므로 하위 호환. `error` 제거는 별도 major 변경에서만 수행 |
| 기존 task 코드형 오류 | `detail.code` + 엔드포인트별 부가 필드 | 기존 형식 유지 | 이번 변경에서 제거/이름 변경 없음 |

클라이언트는 HTTP 상태와 사람이 읽는 메시지 대신 `detail.code`를 분기 기준으로 사용해야 합니다. `message`와 `detail`은 표시용 alias이며 문구 안정성을 보장하지 않습니다.

### 2) CLI 사용성

- 대상: `anygarden`, `anygarden-agent`, `anygarden-client`
- 개선: 옵션 목록/예시 문서 보강
- 호환성 범위:
  - 실행 동작은 동일, 문서/README만 보강됨

## 릴리스별 호환성 계획

| 릴리스 | 요구 사항 |
|---|---|
| 현재 (`anygarden` 0.18.x) | 새 `detail.code/message/detail`을 추가하고 기존 `machine_has_active_agents`의 `detail.error` 유지 |
| 다음 minor | 릴리스 노트에 문자열 `detail`에서 구조화된 `detail`로 이동한 경로와 코드 목록 게시; SDK/CLI 소비자는 `detail.code` 우선 사용 |
| 다음 major 후보 | 저장소/공개 SDK에서 `detail.error` 사용처가 없음을 확인한 뒤 레거시 키 제거 여부 결정 |

## 수동 점검 항목 (수동 릴리스 전)

- [x] `MACHINE_HAS_ACTIVE_AGENTS` 발생 시 `detail.code` 및 레거시 `detail.error` 응답 확인 (`test_machines_api.py`)
- [x] `MACHINE_OFFLINE` 및 `MACHINE_NOT_FOUND`의 코드/메시지/호환 detail 확인 (`test_machines_api.py`)
- [x] `TASK_SOURCE_MESSAGE_NOT_FOUND` 구조 확인 (`test_tasks_api.py`)
- [ ] `TASK_ROOM_NOT_FOUND`, `TASK_NOT_FOUND`를 외부 SDK/CLI가 `detail.code`로 분기하는지 통합 확인
- [ ] `anygarden --help`, `anygarden agent --help`, `anygarden client --help`의 최신 옵션 문구와 기존 옵션의 호환성 확인
- [ ] 기존 API 클라이언트에서 `detail.error` 의존 코드가 있다면 점진적 마이그레이션 계획 수립 (현재 1개 항목: `machine delete 409`)
