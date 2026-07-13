# feat(machines): 머신 상세에 감지된 시스템 정보(호스트네임·IP·OS·CPU·RAM) 표시 + description 필드 (#523)

- Commit: `36100db` (36100db1fd4f4fdc61d9ce31983d5dc4b06e4011)
- Author: Changyong Um
- Date: 2026-07-13T16:33:37+09:00
- PR: — (issue #523)

## Situation

머신 상세 화면은 `name`, 유저가 손으로 입력한 `hostname`, 엔진 목록, 에이전트 수만
보여줬다. 실제 머신이 어떤 장비인지(실호스트네임·네트워크 위치·OS·CPU/메모리 규모)를
알 수 없어 운영·식별이 어려웠다. 게다가 DB `machines.cpu_cores`·`memory_gb` 컬럼은
migration 001부터 존재했지만 등록/하트비트 어디서도 채우지 않아 항상 0이었고,
`hostname`은 자동 감지값이 아니라 유저 입력 라벨이라 이름과 실제 의미가 어긋나 있었다.

## Task

- 머신 데몬이 접속(등록) 시 정적 시스템 정보를 1회 수집해 전송하고, 서버가 저장 →
  API 노출 → `AdminMachines` 상세 Info 카드에 표시.
- 수집·표시 필드: hostname(실감지) · lan_ip · os_platform · cpu_cores · memory_gb.
- `hostname`을 유저 입력 폼에서 제거하고 데몬 자동 감지 전용으로 전환.
- 유저용 자유 텍스트 `description`(별칭/메모) 필드 신설, 부제를 `description||hostname`으로.
- 제약: 하트비트 불변(정적 스펙만), 디스크 제외, 표시 위치는 AdminMachines Info 카드만.

## Action

- **machine 수집·프로토콜**
  - `packages/machine/anygarden_machine/sysinfo.py` 신규 — `collect_system_info()`가
    `socket.gethostname()` · `_primary_lan_ip()`(UDP-connect 트릭, 패킷 미전송) ·
    `platform.platform()` · `psutil.cpu_count()` · `virtual_memory()`를 모두 `_safe()`로
    감싸 best-effort 수집(한 필드 실패가 전체를 막지 않음).
  - `protocol/frames.py` — `SystemInfo` 서브모델 + `RegisterFrame.system_info`(옵셔널,
    구버전 데몬 호환) 추가, `protocol/__init__.py`에 export.
  - `daemon.py:_register` — `collect_system_info()` 호출해 프레임에 첨부·로그 요약.
- **cluster 저장·API**
  - `db/models.py` `Machine` — `description`·`lan_ip`·`os_platform` 컬럼 추가,
    `hostname` 의미를 감지값으로 주석 정리.
  - `db/migrations/versions/053_machine_system_info.py` 신규 — `batch_alter_table`로
    3개 nullable 컬럼 추가(SQLite 런타임 DB 호환), 대칭 downgrade, 백필 없음.
  - `ws/machine_handler.py:_apply_system_info` 신규 — 데몬이 보고한 값 중 **의미 있는
    값만** 덮어써(빈 문자열/0/None은 무시) 부분 수집 실패가 기존 값을 훼손하지 않음.
  - `api/v1/machines.py` — `MachineCreate`/`MachineUpdate`에서 `hostname` 제거·
    `description` 추가, `MachineOut`에 신규 5필드 노출, `register_machine`은 `hostname=""`로
    생성 후 `MachineOut.model_validate(machine).model_dump()`로 응답 구성(신규 필드 자동 반영).
- **frontend**
  - `hooks/useMachines.ts` — `Machine` 타입에 5필드 추가, `registerMachine`/`updateMachine`
    시그니처를 `hostname`→`description`으로.
  - `components/AdminMachines.tsx` — 등록 다이얼로그 hostname 입력을 optional description으로
    교체, 좌측 리스트·헤더 부제를 `description||hostname`, Info 카드에 IP·OS·CPU·Memory 행
    추가(미수집 값은 `—`).
- **테스트** — `test_sysinfo.py` 신규, `test_protocol_frames.py`/`test_daemon.py`/
  `test_machine_handler.py`/`test_machines_api.py`/`test_migrations.py` 확장.

## Decisions

`.tmp/plan-523-machine-system-info.md`의 brainstorming 결과를 근거로 확정:

- **정적 스펙만 (실시간 사용률 배제)** — 실시간 사용률은 30초 하트비트 확장 + 프론트
  프로그레스바/폴링까지 표면적이 2~3배. 이번 요청의 본질은 "이 머신이 무엇인가"라는
  식별 정보이지 부하 모니터링이 아니라고 판단. RegisterFrame만 확장.
- **hostname 3분할** — `name`(식별자)/`description`(자유 메모)/`hostname`(감지값)으로
  나눠 의미 중복 제거. 대안(별도 `detected_hostname` 컬럼)은 `name`이 이미 유저 식별자라
  유저 라벨이 2개가 되는 중복이라 기각. hostname은 NOT NULL 유지·빈 문자열 시작으로
  SQLite nullability 변경 마이그레이션 리스크 회피.
- **IP는 데몬 보고 LAN IP** — 서버 peer IP 대안은 NAT/프록시 뒤에서 실제 머신이 아닌
  게이트웨이 IP가 잡혀 의도와 어긋남. LAN 중심 배치라 데몬 로컬 IP가 식별에 부합.
- **부분 수집 실패 안전장치** — `_apply_system_info`가 truthy 값만 덮어써, 한 번 좋은 값을
  저장한 뒤 재등록 때 수집이 실패(0/"")해도 훼손하지 않음.
- **가정**: 다중 NIC 머신은 기본 라우트 인터페이스 IP 1개만 표시. 다중 IP 요구가 생기면
  `lan_ip`를 리스트로 확장(현재 범위 밖) — 이 전제가 깨지면 재검토.

**리뷰 반영**: 적대적 코드 리뷰에서 `_primary_lan_ip()`의 `socket.socket()` 생성이 try
밖에 있어 소켓 생성 실패(seccomp/netns UDP 차단·fd 고갈) 시 예외가 전파돼 "등록을 절대
막지 않는다"는 불변식을 위반할 수 있다는 지적을 받음. 소켓 생성을 try 안으로 옮기고
`collect_system_info`에서도 `_safe(_primary_lan_ip, None)`로 감싸 다른 필드와 대칭을
맞추고, 회귀 테스트 2건(소켓 생성 실패 시 None 반환) 추가.

## Result

- 데몬 접속 시 머신 상세 Info 카드에 실 hostname·LAN IP·OS·CPU·Memory가 표시되고,
  미접속/미수집 값은 `—`로 나타난다. `description`은 부제로 노출·편집 가능.
- 테스트: machine 385 passed, cluster 1256 passed, frontend 445 passed + tsc 빌드 통과.
  신규/확장 테스트가 sysinfo 수집(부분 실패·소켓 생성 실패 포함), SystemInfo 프레임
  왕복, `_handle_register` 저장·부분 실패 미훼손, 마이그레이션 053 up/down, API 생성
  (hostname 없이)·MachineOut 신규 필드·description PATCH를 커버.
- 기존 API 호환: `MachineCreate.hostname` 제거는 Pydantic extra 무시로 회귀 없음(레거시
  클라이언트가 hostname을 보내도 무시되고 ""로 저장).
- 범위 밖(후속 여지): 실시간 사용률·디스크·토폴로지 패널 표시, 머신 description 인라인
  편집 UI(현재는 생성 시 입력 + updateMachine API 준비 상태).
