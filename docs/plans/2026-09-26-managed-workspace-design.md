# 기본 작업 공간 조회

승인 범위: 에이전트의 실제 실행 머신과 작업 디렉터리, 보존된 파일의 읽기 전용 탐색과 텍스트 미리보기. 외부 폴더 실행(#491), 파일 수정/다운로드, 머신 간 파일 복제는 이 단위에 포함하지 않는다. 기존 파일 설정 API는 실행 시 배포할 manifest이며 실제 작업 파일 API와 분리한다.

## 조건표

| 조건 | 표시와 동작 |
| --- | --- |
| 관리자, 배치된 온라인 지원 머신, 런타임 경로 보고 있음 | 실제 머신/경로/엔진/권한/확인 시각, 새로고침, 폴더 탐색과 텍스트 미리보기 |
| 아직 배치되지 않음 / 실행한 적 없음 | 준비 전 상태. 추정 경로나 빈 디렉터리를 보여주지 않음 |
| 머신 연결 끊김 / 기능 미지원 버전 | 연결 또는 업데이트 안내, 재시도 |
| 에이전트 중지, 머신 온라인 | 마지막 런타임 보고라는 표시와 보존된 관리 공간 읽기 |
| 재배치 / 요청 중 실행 세대 변경 | 이전 결과 폐기, 현재 상태로 재조회 |
| 다른 에이전트/폴더/파일 선택 중 늦은 응답 | 숫자 요청 세대와 AbortController로 폐기 |
| 일반 사용자/게스트 | 기존 관리자 설정 권한 유지, 파일 API 403 |
| 외부 폴더 | 기존 요청/승인/철회와 미지원 안내 유지 |
| 비밀 파일/링크/특수 파일 | 목록에서 비밀 파일 제외, 링크는 열기 불가, 직접 조회도 거부 |

## 원천과 전송

현재 RoomExecutionAdapter는 관리 root/workspace가 디렉터리이면 이를, 아니면 root를 엔진 cwd로 선택한다. 런타임이 실제 선택한 값만 별도 `.anygarden-workspace.json` receipt에 기록한다. receipt는 상대 경로 `.` 또는 `workspace`, pid, process 시작 시각, generation, engine, permission tier, 보고 시각으로 한정한다. 시작 준비와 각 turn 직전에 갱신하며 새로운 파일이나 credential을 포함하지 않는다. 머신은 receipt의 경로와 실행 증거를 검증하고 자신의 관리 root 내에서만 읽는다. 중지 후 receipt와 작업 파일은 보존되며 UI는 마지막 확인이라고 표시한다.

HTTP 관리자 API → MachineBus의 상관 요청 → 원격 인증 WS 또는 내장 LocalExecutionBackend queue → 같은 MachineDaemon 명령 처리 → 인증된 머신 응답 → API. 클러스터는 머신 host 경로를 직접 읽거나 계산하지 않는다. 별도 `managed_workspace_browse_v1` capability를 가진 머신만 이 경로를 광고한다. 외부 폴더 capability는 추가하지 않는다.

`managed_workspace_request` / `managed_workspace_result`는 request_id, agent_id, generation을 공유한다. 명령은 list/read, 상대 path, directory cursor만 받는다. MachineBus는 대기 요청 수와 timeout을 제한하고, 인증된 machine_id/agent_id/generation의 일치, 취소와 연결 종료를 처리한다. API는 응답 직전에 placement와 generation을 다시 확인한다. 응답 파일 내용은 DB나 로그에 저장하지 않는다.

## API

- `GET /api/v1/agents/{id}/workspace?path=&cursor=`: 메타데이터와 한 단계 디렉터리 목록. 경로가 준비되지 않은 경우 명시 상태를 반환한다.
- `GET /api/v1/agents/{id}/workspace/file?path=...`: 같은 메타데이터와 제한된 UTF-8 텍스트 미리보기. 바이너리/큰 파일은 미리보기 불가 상태.
- 관리자만 접근. 기존 `/files` 배열 응답이나 설정 저장 계약은 변경하지 않는다.
- 목록은 이름 순서, 최대 200개와 next_cursor. preview 최대 64 KiB. 한 디렉터리를 탐색하는 비용도 상한을 둔다.
- 응답은 machine 이름/ID, actual state, engine, runtime 보고 cwd/권한/세대/시각, 목록 또는 미리보기와 상태를 포함한다. 클라이언트는 호스트 절대경로를 입력할 수 없다.

## 파일 경계

agent_id와 모든 상대 경로 segment를 검증한다. 절대경로, `..`, `.`, 역슬래시, 빈 중간 segment, 제어문자, 과도한 길이/깊이는 거부한다. 숨김 디렉터리와 비밀 파일(`.env*`, auth/credentials, key/certificate, 내부 manifest/runtime/profile 등)은 목록과 직접 읽기 모두 거부한다. 안전한 숨김 설정 파일은 명시 허용한다.

POSIX는 관리 root부터 디렉터리 fd와 O_NOFOLLOW로 각 segment를 열고, final descriptor의 타입을 검사한다. symlink, hardlink, FIFO/device/socket을 읽지 않는다. 파일 읽기 중 경로 변경에도 fd 경계가 유지되어야 한다. 기존 safefs 쓰기 헬퍼를 그대로 읽기 방어로 사용하지 않는다. 안전한 Windows reparse 경계가 구현되지 않은 플랫폼은 명시적으로 미지원 상태를 반환한다.

관리 공간은 해당 머신의 디스크에 보존된다. 머신 재배치 시 임의 산출물이 자동 복사된다는 보장은 하지 않는다. Codex의 정상 context bridge symlink도 자동으로 따라가지 않는다.

## 구현과 검증 순서

1. 런타임 receipt 및 머신의 안전한 파일 조회 모듈, 임시 root 기반 파일/비밀/경로/링크/특수파일 테스트.
2. 프레임과 broker, 원격 및 내장 동일 handler 경로, timeout/취소/오래된 응답/다른 머신·세대 테스트.
3. 관리자 API와 placement 재검증, 파일 설정 API와의 분리 및 기존 권한 회귀.
4. 기존 WorkspacePanel에 기본 공간 조회 패널 연결. 로딩/오류/재시도/폴더·파일 선택/에이전트 전환 회귀.
5. 중지·재시작 후 파일 보존, 실제 임시 디스크→daemon→transport→API→UI 계약 통합 검증.

## 설치 및 배포 호환성

이 기능을 배포할 때는 동일 변경을 포함하는 cluster/server, `anygarden-machine`, `anygarden-agent` 패키지를 함께 업데이트해야 한다. 클러스터의 조회 API와 내장 노드가 새 machine 모듈/프레임을 import하므로, 새 서버 코드에 기존 공개 machine 패키지만 섞어 설치하는 구성은 지원하지 않는다. 별도 원격 머신도 새 daemon capability와 새 agent runtime receipt가 모두 필요하다. 머신 재연결 후 에이전트를 새 런타임으로 다시 시작해야 경로가 보고된다. 구버전 원격 머신은 unsupported, 새 머신의 구버전 런타임은 not_ready로 표시한다. 이번 작업은 소스와 격리 검증이며 패키지 게시나 사용자 머신 업데이트를 수행하지 않는다.

현재 안전한 파일 탐색은 Linux/macOS의 POSIX descriptor 지원 환경에 한정한다. Windows는 같은 UI에서 명시 미지원 상태를 표시하며, reparse-safe handle 구현 전에 경로 검사만으로 탐색을 활성화하지 않는다.

## 검증 결과

- 실제 임시 파일로 숨김 인증/환경 파일, 경로 이탈, 부모·leaf symlink, hardlink, FIFO, 디렉터리 교체, 200개 페이지 경계, 64 KiB 제한과 폴더 스캔 상한 검증.
- Codex/Pi 실제 RoomExecutionAdapter와 격리된 CLI 실행기로 runtime receipt의 선택 경로와 turn cwd 일치, 에이전트/데몬 재시작 준비 후 파일 보존 검증. 실제 LLM을 호출하지 않는다.
- 실제 machine-token 검증과 `ws_machine` 수신 루프(소켓 전송만 메모리 대역), 실제 내장 LocalExecutionBackend 큐, 동일 daemon handler를 통해 임시 디스크→관리자 API 왕복 검증. 클러스터 직접 파일 읽기는 사용하지 않는다.
- 관리자/비관리자 접근, 기존 manifest API 분리, 배치·세대 변경, 다른 머신/에이전트/세대 응답, 32개 대기 상한, 전송 및 응답 timeout, 취소·끊김의 pending 정리 검증.
- UI의 폴더→텍스트→뒤로가기, 링크 접근 불가, 로딩/실패/재시도/빈 폴더/오프라인, 페이지 중복 제거, 에이전트 A→B→A 및 다른 파일 선택 후 늦은 JSON 응답 폐기 검증.
