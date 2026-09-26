# Pi 로컬 방의 기본 작업·스킬 도구 연결

사용자가 승인한 멀티에이전트 기능 완성 범위에서 Pi의 기존 self MCP 작업·스킬 도구를 연결한다. 새 사용자 옵션과 방 메시지 도구는 추가하지 않는다. 검증은 실제 서버 인증·도구 계약과 Pi 0.85.1의 extension 등록 경로를 사용하되 모델을 호출하지 않는다.

## 확인한 계약과 선택

실제 배포 패키지는 `@earendil-works/pi-coding-agent@0.85.1`이다. 이전 `@mariozechner` 이름에는 이 버전이 없다. 공식 npm 패키지의 docs/extensions.md와 dist/core/extensions 코드를 확인했다. extension factory와 registerTool을 지원하며, execute에서 예외를 던져야 Pi가 도구 실패로 기록한다. MCP의 isError를 그대로 반환하면 성공으로 기록하므로 변환해야 한다. extension factory 오류는 CLI가 수집하고 계속할 수 있어 준비 검사를 Python room adapter에서 수행한다.

권장 구조는 local RoomExecutionAdapter.start의 인증된 MCP 목록 조회와 비밀 없는 설정 준비, RoomPiRuntime의 명시적인 extension 로드다. extension 소스는 agent 패키지에 포함한 신뢰 코드만 사용하고, 동적 스키마/주소는 에이전트별 JSON에 저장한다. 생성된 서버 문자열을 JavaScript 코드로 실행하지 않는다. scheduler는 Pi의 self MCP 토큰을 기존 agent token 캐시로 발급한다. Codex 등의 기존 MCP manifest 합성은 유지한다.

scheduler에서 사용자 편집 가능한 files_map에 extension 코드를 합성하는 대안은 관리 파일과 충돌하고 Pi 코드 소유권이 서버로 번진다. 외부 범용 MCP 플러그인 도입은 추가 의존성과 지원 범위를 늘린다. 이번에는 기존 서버의 9개 도구만 직접 연결한다.

## 조건표

| 조건 | 준비·노출·실행 계약 |
| --- | --- |
| 로컬 Pi standard/trusted | 명시적인 ChatClient 서버 주소에서 self MCP 토큰으로 tools/list 확인 후 기존 9개 도구 등록 |
| 로컬 Pi restricted | 기존 read,grep,find,ls allowlist 유지. self MCP 쓰기 도구/토큰/extension은 추가하지 않음 |
| remote/federation PiRuntime | --no-extensions와 서버 토큰 차단 유지. 로컬 bridge 설정·소스 경로 전달 없음 |
| Codex 및 기존 엔진 | 기존 manifest와 토큰 발급·인증 동작 유지 |
| 토큰 누락·잘못된 종류·401/403 | 준비 실패를 명확히 전달. ChatClient의 transport token으로 대체하지 않음 |
| 목록 조회 실패·시간 초과·잘못된 schema | 에이전트 준비 실패. 목록 없는 상태를 도구 지원으로 보고하지 않음 |
| 실행 성공 | MCP content와 structuredContent를 Pi content/details로 변환 |
| HTTP·JSON-RPC·MCP isError | 토큰을 제거한 제한된 오류 설명을 throw. Pi 도구 실행 실패로 기록 |
| 취소·실행 시간 초과 | AbortSignal과 제한 시간을 HTTP 요청에 연결. 무응답 프로세스/요청을 남기지 않음 |
| 다른 에이전트의 작업·스킬 변경 | 서버 resolve_agent_id 및 기존 소유권/Capability 검증 적용. bridge에서 agent_id를 선택하게 하지 않음 |
| 자격증명과 네트워크 | self MCP bearer만 child env에 전달. argv/설정/로그에 저장하지 않음. redirect 거절. 사용자 입력 endpoint override 없음 |
| 방 메시지 도구 | 현 MCP에 존재하지 않으므로 이번 구현에서 추가·완료 처리하지 않음 |

현재 도구는 create_skill, update_skill, list_my_skills, delete_my_skill, claim_task, mark_task_status, create_task, add_task_blocker, clear_task_blocker다. 서버가 광고하는 스키마를 확인하되 이 집합 밖의 도구는 자동 노출하지 않는다. 필수 도구 누락이나 중복·유효하지 않은 스키마는 준비 실패로 취급한다. 추가로 광고된 미지원 도구는 노출하지 않는다.

## 검증

토큰 발급·롤백/캐시·기존 Codex manifest 회귀, 실제 MCP 목록 및 작업/스킬 성공·소유권 거절, Node에서 extension 등록·성공·오류·취소, Pi 버전 고정 loader 등록, restricted와 federation 격리를 확인한다. CLI/확장 로더 검사는 모델 호출 없이 수행한다. 기존 방 실행 테스트는 self MCP 준비를 명시적인 fixture로 대체하고 새 통합 테스트가 실제 인증·HTTP 왕복을 검증한다.

## 구현 검증 결과

- 기존 room/Pi runtime와 신규 bridge 단위 테스트 65개 통과. MCP 목록 준비·토큰 종류·잘못된 주소·HTTP/JSON-RPC/도구 실패·취소/시간 초과·symlink·restricted/federation 경계를 확인했다.
- 실제 localhost FastAPI MCP 서버에서 scheduler 토큰을 발급하고 Node extension을 통해 9개 도구 모두 성공 호출했다. 다른 에이전트 작업·스킬 수정 거절, 토큰 소유자, 캐시 재사용·롤백·restricted 제외를 통합 테스트 2개로 확인했다. 기존 MCP default/manifest 테스트 18개도 통과했다.
- 공식 npm 0.85.1을 격리 설치하고 실제 extension loader와 createAgentSession으로 도구를 등록했다. standard에는 기존 기본 도구와 self 도구 9개, restricted에는 read/grep/find/ls만 활성화됨을 확인했다. prompt/모델 요청은 실행하지 않았고 probe의 네트워크 요청 수는 0이다. 증거: `.tmp/platform-completion/pi-loader-check.json`.
- agent wheel을 빌드하여 `pi_self_tools.py`와 `pi_self_tools.mjs`가 함께 포함됨을 확인했다. 외부 MCP 플러그인이나 런타임 npm 의존성은 추가하지 않았다.
