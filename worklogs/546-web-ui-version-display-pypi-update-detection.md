# feat(system): 웹 UI 버전 표시 + PyPI 업데이트 감지·알림 (#546)

- Commit: `8c7d9f7` (8c7d9f7)
- Author: Changyong Um
- Date: 2026-07-23
- PR: #546 (issue)

## Situation

웹 UI에 cluster 서버 버전이 어디에도 표시되지 않아 "지금 뭐가 떠 있는지" 운영·디버깅 시 알 방법이 없었다. 게다가 `anygarden/__init__.py`·`anygarden_machine/__init__.py`의 `__version__`이 하드코딩된 stale 값(0.2.0/0.1.0, 실제 0.15.0/0.11.0)이었고, `RegisterFrame`에 `daemon_version` 필드가 없어 머신이 버전을 보고하지 못해 Admin > Machines의 Version이 항상 `-`로 떴다(서버 수신부와 프론트 슬롯은 이미 배선돼 있었음). 업그레이드가 최신인지 확인할 수단도 없었다.

## Task

- 서버 버전을 웹 UI에 노출(모든 로그인 유저), stale `__version__`을 실제 설치 버전으로 정리
- 머신이 `daemon_version`을 보고하도록 보완 → Admin에 실제 머신 버전 표시
- 현재 vs PyPI 최신을 비교해 admin에게 업데이트 알림. 조회는 수동(버튼), 결과는 DB 캐시로 재시작 후에도 유지, 적용은 명령 안내만(실행 X)
- 조회 로직을 "수동 → 자동(백그라운드)" 전환이 재작업 없이 가능하도록 계층 분리
- 제약: systemd `Environment=`류 회피 불필요하나 PyPI 실패가 요청을 블로킹하지 않을 것, 버전 비교는 문자열 비교 금지

## Action

버전 source 정리:
- `packages/cluster/anygarden/__init__.py`, `packages/machine/anygarden_machine/__init__.py` — `importlib.metadata.version()` + `PackageNotFoundError` fallback(`0.0.0+dev`)

머신 버전 보고:
- `packages/machine/anygarden_machine/protocol/frames.py` — `RegisterFrame.daemon_version: str | None`
- `packages/machine/anygarden_machine/daemon.py:_register()` — `daemon_version=__version__` 전송 (서버 `machine_handler`는 이미 수신·저장)

백엔드(cluster):
- `anygarden/system/version_service.py` — `get_local_version` / `fetch_pypi_latest`(httpx, 실패→None 흡수) / `is_update_available`(`packaging.version.parse`)
- `anygarden/system/version_store.py` + `db/models.py`의 `VersionCheck` + migration `054_version_checks.py` — per-package 캐시(실패 시 last-known latest 보존)
- `anygarden/api/v1/system.py` — `GET /version`(forbid_guest), `GET /updates`(admin, 캐시만), `POST /check-updates`(admin, PyPI 조회+upsert). `app.py`에 라우터 등록
- `pyproject.toml` server extra에 `packaging>=23`

프론트엔드:
- `hooks/useSystemVersion.ts` — `useSystemVersion`(전 유저) + `useUpdateStatus`(admin, 캐시 읽기 + refresh POST)
- `components/Sidebar.tsx` — 푸터 서버 버전, admin nav "System" + update 배지
- `pages/AdminSystemPage.tsx` + `components/AdminSystem.tsx` — 현재/최신/확인시각 표 + [업데이트 확인], 적용 명령 안내
- `App.tsx` — `/admin/system` 라우트

## Decisions

계획(`.tmp/plan-546-system-version-display.md`) 및 설계문서(`docs/plans/2026-07-23-...`)에서 브레인스토밍으로 확정:

- **패치 자동화 수준**: 감지·알림만 채택. one-click(machine/전체)은 서버 self-restart supervisor·원격 트리거 RCE 표면·롤백 설계가 모두 필요 → 가치 80%를 리스크 10%로 얻는 "알림만"이 최소. 적용은 UI가 실행하지 않고 명령 문자열만 노출.
- **PyPI 조회 시점**: 수동(admin 버튼) 먼저. 자동 상시 조회는 air-gap/예상 못한 egress 유발. 결정타는 **"UI는 캐시만 읽는다"는 계층 분리** — 이 하나로 수동/자동이 대칭이 되어, 나중에 스케줄러가 같은 `fetch_pypi_latest`+`upsert`로 캐시를 채우면 UI·엔드포인트 불변(재작업 0). 사용자가 "수동→자동 전환 가능?"을 물어 이 구조를 명시적으로 채택.
- **캐시 저장소**: DB 영속화(사용자 결정) vs 인메모리. 재시작 후 마지막 확인 결과 유지 + 자동 모드 전환 시 자연스러움을 위해 DB, 비용은 migration 1개.
- **버전 노출 대상**: 전 유저(사용자 결정). 버전은 무해·유용, 적용성 액션(배지/버튼)만 admin.
- **버전 비교**: `packaging.version.parse` — 문자열 비교는 0.9 > 0.10으로 오정렬. dev/local 버전(`0.0.0+dev`)은 릴리스와 비교 불가로 배지 억제.

정정: 설계 초안은 "머신 버전 이미 표시 중"이라 봤으나, 구현 중 `RegisterFrame`에 `daemon_version`이 없어 실제로는 미보고(항상 `-`)임을 확인 → 머신 보고 추가로 스코프 확장.

가정(위반 시 재검토): 서버는 자기 버전과 각 머신 `daemon_version`만 신뢰성 있게 안다(agent 개별 버전은 후속). 백그라운드 자동 조회는 범위 밖(후속).

## Result

- cluster 전체 1275 passed(+신규 version_store/service/system_api), migration 15 passed(head 053→054 assertion 갱신), machine 390 passed(+daemon_version 보고), 프론트 445+5 passed, `npm run build`(tsc) 통과
- ruff: 추가 코드 클린(잔존 F541/unused-import은 `register`/`config`의 기존 이슈로 무관, 미변경)
- 실측: `fetch_pypi_latest("anygarden")` → 실제 PyPI `0.15.0`(JSON `info.version` 형태 확인), 존재하지 않는 패키지 → None(에러 흡수)
- 미결: 실제 systemd/브라우저 라이브 확인은 배포 환경에서 수행 필요. 백그라운드 자동 PyPI 조회 스케줄러는 후속 이슈
