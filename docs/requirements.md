# 요구사항 카탈로그

Anygarden이 보장해야 하는 동작의 목록이다. 각 행은 **어떤 계층의 테스트가 이 요구를 검증하는지** 선언하고,
CI의 `Requirement coverage` 잡이 그 선언을 강제한다(`scripts/req_report.py --require-coverage`).

| 검증 값 | 뜻 | 강제 방식 |
|---|---|---|
| `pytest` | `@pytest.mark.req("ID")`가 붙은 pytest가 통과해야 한다 | cluster·agent JUnit |
| `vitest` | 제목에 `[ID]`가 들어간 vitest가 통과해야 한다 | vitest JUnit |
| `e2e` | 제목에 `[ID]`가 들어간 Playwright 테스트가 통과해야 한다 | Playwright JUnit |
| `build` | 프론트엔드 타입체크·빌드가 검증한다 | `npm run build` |
| `gap` | 아직 구현되지 않았다(부분 구현이면 테스트 계층과 함께 적는다) | 매 실행 "Known gaps"로 보고 |

- 새 요구는 행을 추가하고 테스트를 태그한다. 테스트 없이 `pytest` 등으로 선언하면 CI가 실패한다.
- 카탈로그에 없는 ID를 태그해도 CI가 실패한다.
- `gap`을 구현하면 값을 실제 계층으로 바꾼다.
- 라이브(실제 엔진) 검증은 `benchmarks/`가 맡는다. 이 카탈로그는 결정적 테스트의 범위다.

## 인증·계정·게스트 (AUTH)

| ID | 요구 | 검증 |
|---|---|---|
| AUTH-01 | 같은 이메일로 두 번째 가입하면 409. 첫 사용자만 admin이 된다 (동시 가입 포함) | pytest |
| AUTH-02 | 잘못된 자격증명은 401, 반복 실패 시 제한된다 | pytest |
| AUTH-03 | 일반 user·guest·agent 토큰은 모든 admin 라우트에서 403 | pytest |
| AUTH-04 | admin 권한을 회수하면 즉시 admin 라우트가 거부된다 | pytest |
| AUTH-05 | 초대 토큰 원문은 POST 응답에만 나오고 GET은 토큰·해시를 반환하지 않는다 | pytest |
| AUTH-06 | 회수·만료·소진된 초대로 수락하면 401. `max_uses`는 동시 수락에서도 넘지 않는다 | pytest |
| AUTH-07 | 게스트 토큰으로 다른 룸에 접근하면 403. 게스트 JWT는 초대 만료 시각을 넘지 않는다 | pytest |
| AUTH-08 | 초대를 회수하면 그 초대로 들어온 게스트 세션이 거부된다 | pytest |
| AUTH-09 | 비멤버가 초대를 만들려 하면 룸 존재 여부와 무관하게 403 (존재 노출 없음) | pytest |

## 프로젝트·룸 (ROOM)

| ID | 요구 | 검증 |
|---|---|---|
| ROOM-01 | 프로젝트 삭제·조회는 권한 있는 사용자로 제한된다 | pytest |
| ROOM-02 | 프로젝트를 삭제하면 하위 룸·참여자·메시지·파일·초대가 제거되고 DM은 남는다 | pytest |
| ROOM-03 | 서브룸은 생성 시 부모 룸의 참여자를 가진다 | pytest |
| ROOM-04 | 룸을 보관하면 열린 WS가 닫히고 하위 룸도 보관된다 | pytest |
| ROOM-05 | 마지막 admin/owner 제거는 409. 사용자는 스스로 룸을 나갈 수 있다 | pytest, vitest |
| ROOM-06 | 같은 사용자를 두 번 추가해도 Participant 행은 하나다 | pytest |
| ROOM-07 | 룸에 없는 에이전트를 대표로 지정하면 400 | pytest |
| ROOM-08 | 한도 초과 파일 업로드는 거부되고 게스트는 403 | pytest |
| ROOM-09 | 고정 순서는 새로고침 후에도 유지되고 사용자의 다른 세션에 반영된다 | pytest |

## 메시징·실시간 (MSG)

| ID | 요구 | 검증 |
|---|---|---|
| MSG-01 | 잘못된 토큰은 4001, 비멤버는 4003으로 accept 전에 닫힌다 | pytest |
| MSG-02 | 에이전트의 두 번째 연결은 첫 연결을 4040으로 닫는다. 사용자의 두 번째 탭은 닫지 않는다 | pytest |
| MSG-03 | 동시 전송에도 룸별 seq는 중복·빈 번호 없이 증가한다 | pytest |
| MSG-04 | `since_seq=N`으로 재접속하면 N 이후 메시지를 순서대로, 빠짐없이 받는다 (10,000개까지) | pytest, vitest |
| MSG-05 | 답글에 답글을 달거나 다른 룸의 루트를 쓰면 400/404 | pytest, e2e |
| MSG-06 | 검색은 호출자(게스트 포함)가 접근할 수 없는 룸의 메시지를 반환하지 않는다 | pytest |
| MSG-07 | 같은 인터랙션에 두 번째 응답은 거부된다 | pytest |
| MSG-08 | 사용자는 자신이 보낸 메시지를 수정·삭제할 수 있다 | pytest, vitest |

## 에이전트 관리 (AGT)

| ID | 요구 | 검증 |
|---|---|---|
| AGT-01 | 제거된 엔진(claude-code, gemini-cli, openhands)으로 생성하면 422와 이전 안내 | pytest |
| AGT-02 | pi-cli 에이전트는 provider 없이 생성·수정할 수 없다 | pytest |
| AGT-03 | 머신이 오프라인이거나 엔진 미지원, 용량 초과면 생성은 409 | pytest |
| AGT-04 | 아바타만 바꾸면 재시작하지 않는다. 권한 수준 변경은 감사 기록을 남긴다 | pytest |
| AGT-05 | admin이 아니면 권한 수준을 바꿀 수 없고, 허용 값 밖은 422 | pytest |
| AGT-06 | 호출 이력이 있으면 삭제는 409. 아니면 중지·토큰 캐시 삭제·DM 삭제까지 수행 | pytest |
| AGT-07 | 경로 탈출을 시도하는 파일 쓰기는 400 | pytest |
| AGT-08 | admin은 설정을 유지한 채 에이전트의 엔진을 바꿀 수 있다 | pytest, vitest |
| AGT-09 | 쿼터가 소진된 에이전트는 라우팅에서 빠지고 룸에 사유가 표시된다 | pytest |

## 엔진 실행 (ENG)

| ID | 요구 | 검증 |
|---|---|---|
| ENG-01 | 추적 턴에서 엔진이 빈 응답을 내면 failed로 끝나고 사용자 안내가 나간다. 비추적 턴은 안내하지 않는다 | pytest |
| ENG-02 | `skipped`는 턴을 `agent_skipped`로 완료하고 재시도하지 않는다 | pytest |
| ENG-03 | 완료 없이 끝난 lease는 만료 후 최대 1회 재시도된다 | pytest |
| ENG-04 | 턴 타임아웃 우선순위가 지켜지고 supervisor 타임아웃은 항상 턴보다 길다 | pytest |
| ENG-05 | 취소된 턴은 재시도하지 않고, 위임 턴이면 취소 안내를 남긴다 | pytest |
| ENG-06 | 룸 세션은 respawn 후 재개되고, 세션이 없으면 새 턴으로 대체된다 | pytest |
| ENG-07 | Pi는 관리형 고정 버전으로만 실행된다 (`ANYGARDEN_PI_USE_PATH=1` 제외) | pytest |
| ENG-08 | 같은 룸에서 에이전트당 동시에 실행되는 턴은 1개다 | pytest |
| ENG-09 | 사용자는 일반 룸에서 진행 중인 턴을 취소할 수 있다 | pytest, vitest |

## 에이전트 협업 (COL)

| ID | 요구 | 검증 |
|---|---|---|
| COL-01 | 에이전트가 2명 이상인 룸에서 멘션 없는 사람 메시지에는 아무도 답하지 않는다 | pytest |
| COL-02 | A만 멘션하면 B는 답하지 않는다 | pytest |
| COL-03 | `@everyone`은 모든 에이전트를 깨우고, 에이전트가 보낸 `@everyone`도 깊이·예산에 포함된다 | pytest |
| COL-04 | 2단계 이상 peer 호출은 멘션이 제거되고 '전달 안 됨'으로 기록된다 | pytest |
| COL-05 | 사용자 턴 하나에서 peer handoff는 최대 8회다 | pytest |
| COL-06 | `ask_peer`로 3명에게 물으면 호출자는 정확히 1번, 3명 모두의 결과(또는 타임아웃)와 함께 깨어난다 | pytest |
| COL-07 | 본문에 없는 멘션 메타데이터를 가진 스레드 답글은 에이전트를 깨우지 않는다 | pytest |
| COL-08 | 1:1 DM에서는 멘션 없이도 에이전트가 답한다 | pytest |
| COL-09 | 에이전트는 로스터에서 자신과 다른 참여자를 구분한다 (화자 신원 혼동 없음) | pytest |

## 태스크·실행·목표 (EXE)

| ID | 요구 | 검증 |
|---|---|---|
| EXE-01 | 선행 태스크가 미해결·실패면 claim·완료할 수 없다 | pytest |
| EXE-02 | 같은 예약 슬롯이 다시 전달되면 기존 태스크를 반환하고 새로 만들지 않는다 | pytest |
| EXE-03 | 이전 실행이 진행 중(blocked 포함)이면 다음 목표 실행은 겹치지 않고 기다린다 | pytest |
| EXE-04 | 목표는 3회 연속 실패하면 자동 정지된다 | pytest |
| EXE-05 | 60s 미만 간격이나 잘못된 시간대는 거부된다 | pytest |
| EXE-06 | 외부 동작은 명시적 승인 후 최대 1회만 실행되고, 반려 후에는 실행되지 않는다 | pytest |
| EXE-07 | 실행을 취소하면 진행 중 턴이 멈추고 이후 새 태스크·턴이 시작되지 않는다 | pytest |
| EXE-08 | 완료가 재전달되어도 태스크·결과·보고는 바뀌지 않는다 | pytest |
| EXE-09 | 담당자의 추가 정보 질문은 운영실과 인박스에 나타나고, 답하면 담당자가 재개한다 | pytest |
| EXE-10 | 실패한 하위 작업은 재시도할 수 있고, 한도를 넘으면 중단 사유를 보고한다. 부작용 전에 일시적 모델 오류로 실패한 턴(실행 턴과 일반 방 턴 모두)은 lease 재시도와 별개 예산으로 최대 3회, 30·60·120초 간격으로 자동 재시도된다 | pytest |
| EXE-11 | 총괄이 같은 프로젝트의 서브룸 담당자에게 위임하면, 담당자는 실행에 묶인 자기 턴으로 그 태스크를 받는다 | pytest |
| EXE-12 | 위임된 태스크에는 목표, 원래 사용자 요청, 총괄의 지시가 함께 전달된다 | pytest |
| EXE-13 | 실행이 관리하는 태스크를 일반 태스크 API로 바꾸려 하면 409 `PROJECT_EXECUTION_TASK_MANAGED` | pytest |
| EXE-14 | 선행 작업이 끝나기 전에는 의존 작업 담당자를 깨우지 않고, 끝나면 선행 결과와 함께 깨운다 | pytest |
| EXE-15 | 총괄은 하위 결과를 받아 실행을 완료하고 운영실에 최종 보고를 남긴다. 완료 후 실행은 추가 호출을 받지 않는다 | pytest |
| EXE-16 | 실패한 작업 턴은 종료 영수증과 에이전트 실행 상태가 확인될 때만 사용자가 재시도할 수 있고, 같은 요청은 한 번만 처리된다 | pytest |
| EXE-17 | 위임 횟수 한도를 넘는 위임과 지원하지 않는 권한·한도 선언은 거부된다 | pytest |
| EXE-18 | 질문 턴이 끝나기 전에 답이 와도 재개 턴이 작업을 이어받는다. 옛 턴의 상태 변경은 `TASK_TURN_SUPERSEDED`로 거부된다 | pytest |
| EXE-19 | 담당자가 사유를 적어 막은 태스크는 공개 오류 `TASK_BLOCKED`로 보이고 원문은 노출되지 않는다 | pytest |
| EXE-20 | 외부 동작 승인을 거절하면 오류는 `APPROVAL_REJECTED`, 총괄이 사유와 함께 깨어난다(기본 동작, 제품 결정 대기) | pytest |
| EXE-21 | 담당자가 사유를 적어 태스크를 막거나(`TASK_BLOCKED`) 담당자의 엔진 호출이 실패해 멈추면(`MODEL_EXECUTION_FAILED`, 프로세스 종료가 확인된 경우) 총괄이 공개 사유와 함께 한 번 깨어나, 지침을 붙여 재개(`resume_project_task`, 두 경우 합쳐 태스크당 2회)·질문·중단을 결정한다. 승인 후 재개된 턴(승인된 관리형 작업을 실행하려던 턴)이 엔진에서 실패한 경우도, 그 태스크의 승인이 모두 결정되어 있고(대기 없음) 승인된 작업의 결과가 확실할 때(아직 미실행이거나 결과가 기록된 실행)에는 같은 방식으로 재개할 수 있으며, 재개된 담당자는 승인이 이미 허용됐고 실행 여부가 어떤지 안내받는다(서버 멱등성으로 두 번 전송되지 않음). 결과를 알 수 없는 실패(`PROCESS_OUTCOME_UNKNOWN`), 대기 중인 승인, 결과를 알 수 없는 승인 작업(`ACTION_OUTCOME_UNKNOWN`), 거절된 승인, 자동 재시도 소진은 사용자 몫으로 남는다 | pytest |
| EXE-22 | 작업을 배정·재배정·재요청하거나 의존 작업이 깨어나면 담당 에이전트는 배정 메시지를 lease가 담긴 전달로만 받고, 방의 다른 참가자는 실시간으로 받는다. lease를 받기 전 attempt에 온 lifecycle 프레임은 attempt를 시작 상태로 옮기지 않는다 | pytest |

## 인박스·알림 (INB)

| ID | 요구 | 검증 |
|---|---|---|
| INB-01 | 한 태스크의 진행과 완료는 인박스에서 항목 하나로 보인다 | pytest, vitest |
| INB-02 | 열린 질문·승인이 정보성 항목보다 위에 정렬된다 | pytest, vitest |
| INB-03 | 원 프로젝트에 접근 권한이 없는 사용자는 그 항목을 보지 못하고 산출물에 403 | pytest |
| INB-04 | 새로고침·재로그인 후에도 대기 중인 질문·승인이 그대로 남는다 | e2e |
| INB-05 | 인박스에서 바로 답할 때 작성 중인 초안이 유지되고 원 요청으로 이동할 수 있다 | e2e |
| INB-06 | 새 항목은 새로고침 없이 15초 안에 나타난다 | vitest |

## 메모리 (MEM)

| ID | 요구 | 검증 |
|---|---|---|
| MEM-01 | 낡은 `expected_revision`으로 쓰면 409와 현재 스냅샷 | pytest |
| MEM-02 | 에이전트는 임시 룸의 메모리에 쓸 수 없다 | pytest |
| MEM-03 | 배치 머신·generation이 다르면 머신 쓰기는 `MEMORY_RUNTIME_STALE` | pytest |
| MEM-04 | admin 수정은 `session_epoch`를 올려 세션 키를 바꾼다 | pytest |
| MEM-05 | 공유 문맥 블록은 해당 룸의 파일만 읽는다 (형제 룸, 심볼릭 링크 제외) | pytest |
| MEM-06 | 룸 커서는 respawn 후에도 유지되고 손상 파일은 빈 값으로 시작한다 | pytest |
| MEM-07 | 프로젝트 A 룸의 메모리·자료가 프로젝트 B 룸에 섞이지 않는다 | pytest |

## 머신·노드 (NODE)

| ID | 요구 | 검증 |
|---|---|---|
| NODE-01 | 배치는 오프라인·WS 미연결·`max_agents` 도달 머신을 고르지 않는다 | pytest |
| NODE-02 | 엔진을 지원하는 머신이 없으면 미배치 사유가 `no_machine_for_engine`이다 | pytest |
| NODE-03 | 같은 데이터 디렉터리로 `anygarden start`를 두 번 하면 두 번째는 실패한다 | pytest |
| NODE-04 | `--workers 2` 또는 `WEB_CONCURRENCY>1`이면 기동하지 않는다 | pytest |
| NODE-05 | 스탬프 없는 레거시 DB에서는 기동을 거부하고, 새 DB는 head로 스탬프된다 | pytest |
| NODE-06 | 에이전트 종료는 프로세스 그룹 전체를 종료한다 | pytest |
| NODE-07 | drain된 머신에는 새 배치가 일어나지 않는다 | pytest |
| NODE-08 | 회수된 머신 토큰으로는 연결할 수 없다 | pytest |
| NODE-09 | 노드를 종료하면 실행 중 에이전트가 잠금 경합 없이 정리된다 | pytest |
| NODE-10 | 로컬 실행의 일시적 I/O 오류(디스크 가득 참·DB 잠김)는 노드를 멈추지 않고, 지속되면 healthz가 비정상을 보고하며 노드 프로세스가 스스로 종료된다 | pytest |

## 페더레이션 (FED)

| ID | 요구 | 검증 |
|---|---|---|
| FED-01 | 무효·만료된 초대 번들로 수락하면 거부되고 peer가 생기지 않는다 | pytest |
| FED-02 | 고정된 인증서와 다른 mTLS 인증서의 요청은 거부된다 | pytest |
| FED-03 | peer를 회수하면 그 peer의 채널 권한이 모두 비활성화된다 | pytest |
| FED-04 | 예산으로 정지된 에이전트만 있는 채널에 위임하면 `NO_ELIGIBLE_EXECUTOR` (`budget_pause`) | pytest |
| FED-05 | 실행자는 원격 실행 직전에 예산 차단을 다시 확인한다 | pytest |
| FED-06 | 자격증명 파일 없이 `--peer-port`를 쓰면 기동이 실패한다 | pytest |
| FED-07 | 제출 재시도는 `request_id` 단위로 멱등이다 | pytest |
| FED-08 | 운영자가 CLI로 peer 자격증명을 만들 수 있다 | pytest |
| FED-09 | 토폴로지에 연합 peer가 표시된다 | pytest, vitest |

## 사용량·예산 (USE)

| ID | 요구 | 검증 |
|---|---|---|
| USE-01 | 사용량 집계는 admin 전용이다 (그 외 403) | pytest |
| USE-02 | 잘못된 `window`는 24h로 처리된다 | pytest |
| USE-03 | 토큰 정보가 있는 `engine_call_finished`는 원장에 정확히 1행을 남기고, 없는 프레임은 남기지 않는다 | pytest |
| USE-04 | status ≥400 행은 예산 토큰 합계에 포함되지 않는다 | pytest |
| USE-05 | 에이전트 정책의 hard-stop을 넘으면 에이전트가 정지되고 incident가 생긴다 | pytest |
| USE-06 | 예산 hard-stop은 일반 룸 호출도 막는다 | pytest |
| USE-07 | admin은 화면에서 예산 정책을 관리할 수 있다 | pytest, vitest, e2e |

## 관리·시스템 (SYS)

| ID | 요구 | 검증 |
|---|---|---|
| SYS-01 | `/updates`는 외부 호출을 하지 않고 `/check-updates`만 PyPI에 접속한다 | pytest |
| SYS-02 | PyPI 조회가 실패하면 오류를 저장·표시하고 5xx를 반환하지 않는다 | pytest |
| SYS-03 | `/healthz`는 DB 장애일 때만 503이다 | pytest |
| SYS-04 | 모든 en 키에 ko 번역이 있다 | build |
| SYS-05 | 테마 선택은 유지되고 탭 간에 동기화된다 | vitest |
| SYS-06 | 새 버전은 백그라운드에서 자동 감지된다 | pytest, vitest |

## 관리형 워크스페이스 (WS)

| ID | 요구 | 검증 |
|---|---|---|
| WS-01 | 관리형 워크스페이스 탐색·편집은 admin 전용이고 머신 capability가 필요하다 | pytest |
| WS-02 | 유효한 `edit_token` 없는 쓰기는 거부된다 | pytest |
| WS-03 | 영수증은 허용된 두 디렉터리에만 쓰이고 비밀값을 담지 않는다 | pytest |
| WS-04 | 워크스페이스 연결은 룸 또는 전역 승인과 검증을 거쳐야 쓰인다 | pytest |
| WS-05 | 연결 회수와 무효 연결 정리는 감사 기록을 남긴다 | pytest |
| WS-06 | 룸 공유 파일은 해당 룸 경로에 나타나고 삭제 시 제거된다 | pytest |
