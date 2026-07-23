# Design: 웹 UI 버전 표시 + 업데이트 감지·알림

> Date: 2026-07-23 | Status: 설계 승인됨 (구현 계획 대기)

## 1. 배경 / 문제

- anygarden는 3개 PyPI 패키지로 배포된다: `anygarden`(cluster), `anygarden-agent`, `anygarden-machine`. 버전 source of truth는 각 `pyproject.toml`.
- **현재 웹 UI에 cluster 서버 버전이 어디에도 표시되지 않는다.** "지금 뭐가 떠 있지?"를 알 방법이 없어 디버깅·지원이 어렵다.
- machine daemon 버전(`daemon_version`)은 이미 서버로 보고되어 Admin > Machines / 토폴로지 DetailPanel에 표시된다.
- **잠복 버그**: `packages/cluster/anygarden/__init__.py`의 `__version__`이 `0.2.0`, `packages/machine/anygarden_machine/__init__.py`가 `0.1.0`으로 **stale**(실제 0.15.0 / 0.11.0). 하드코딩 문자열이라 릴리스 때 동기화가 누락됐다. machine이 이 값을 보고하면 `daemon_version`이 틀리게 표시될 위험이 있다.
- 업그레이드가 최신인지 운영자가 알 방법이 없다.

## 2. 목표 / 비목표

**목표**
- ① 웹 UI에 서버 버전을 표시 (모든 로그인 유저). stale `__version__`을 실제 설치 버전으로 정리.
- ② 현재 버전 vs PyPI 최신을 비교해 "업데이트 가능"을 admin에게 알림. 조회는 **수동**(admin 버튼), 결과는 **DB 캐시**로 재시작 후에도 유지.
- ②의 로직을 "수동 → 자동(백그라운드 주기 조회)" 전환이 재작업 없이 가능하도록 구조화.

**비목표 (이번 범위 제외)**
- one-click 자동 적용 / 서버 self-restart / 롤백.
- 백그라운드 자동 PyPI 조회 (구조만 열어둠, 후속).
- 실행 중인 agent의 `anygarden-agent` 버전 개별 수집 (서버가 신뢰성 있게 아는 것은 자기 버전과 각 machine의 `daemon_version`뿐).

## 3. 결정 사항 (브레인스토밍 합의)

| 주제 | 결정 | 근거 |
|---|---|---|
| 패치 자동화 수준 | **감지·알림만** (적용은 명령 안내, 실행 X) | 가치 80%를 리스크 10%로. one-click은 서버 self-restart·RCE 표면·롤백 설계가 모두 필요 |
| PyPI 조회 시점 | **수동**(admin 버튼) 먼저, 자동은 후속 | air-gap/예상치 못한 egress 회피. UI는 캐시만 읽으므로 자동 전환 시 재작업 0 |
| 버전 노출 대상 | **모든 로그인 유저**(Sidebar). 업데이트 배지·확인 버튼은 admin 전용 | 버전은 디버깅·지원에 유용한 무해 정보. 적용성 액션만 admin |
| 캐시 저장소 | **DB 영속화** (+ alembic 마이그레이션) | 재시작 후에도 마지막 확인 결과 유지, 자동 모드 전환 시에도 자연스러움 |

## 4. 아키텍처

### 4.1 컴포넌트 개요

```
┌─ 프론트엔드 ────────────────────────────────┐
│ Sidebar 푸터: "anygarden v0.15.0" (모든 유저)  │
│   └ admin & update? → "⬆ v0.16.0" 힌트         │
│ Admin > System: 현재/최신/[업데이트 확인] 버튼 │
└───────────┬─────────────────────────────────┘
            │ GET /system/version  (로그인)
            │ GET /system/updates  (admin, 캐시 읽기)
            │ POST /system/check-updates (admin, PyPI 조회)
┌───────────▼─────────────────────────────────┐
│ api/v1/system.py  (라우터)                     │
│   └ version_service (조회·비교 순수 로직)       │
│        ├ importlib.metadata (로컬 버전)         │
│        ├ httpx → pypi.org/pypi/{pkg}/json       │
│        └ packaging.version (정확 비교)          │
│   └ version_store (DB 캐시 upsert/read)         │
└───────────┬─────────────────────────────────┘
            │
┌───────────▼─────────────────────────────────┐
│ DB: version_check 테이블 (per-package 캐시)    │
└─────────────────────────────────────────────┘
```

### 4.2 백엔드 상세

**B-1. 버전 source 정리 (잠복 버그 수정)**
- `packages/cluster/anygarden/__init__.py`, `packages/machine/anygarden_machine/__init__.py`:
  ```python
  from importlib.metadata import version, PackageNotFoundError
  try:
      __version__ = version("anygarden")      # machine은 "anygarden-machine"
  except PackageNotFoundError:                 # 소스 실행 등 미설치
      __version__ = "0.0.0+dev"
  ```
- 효과: 릴리스 시 pyproject만 올리면 `__version__`이 자동 일치. machine `daemon_version` 보고도 실제값이 됨.

**B-2. `version_service`** (`anygarden/system/version_service.py`, 외부 I/O를 함수 경계로 격리)
- `get_local_version() -> str` — `importlib.metadata.version("anygarden")`, 미설치 시 `0.0.0+dev`.
- `async fetch_pypi_latest(package: str) -> str | None` — httpx GET `https://pypi.org/pypi/{package}/json` → `info.version`. 타임아웃(수 초)·비200·네트워크 오류 시 `None`.
- `is_update_available(current: str, latest: str | None) -> bool` — `packaging.version.parse`로 비교(문자열 비교 금지: 0.9 < 0.10 정확히 처리). `latest`가 None이거나 dev 버전이면 False.
- 대상 패키지: 최소 `anygarden`. 확장 시 `anygarden-machine`(각 machine `daemon_version` 대조용).

**B-3. `version_store`** (`anygarden/system/version_store.py`)
- 테이블 `version_check`: `package`(PK), `latest_version`(nullable), `checked_at`(datetime), `error`(nullable str).
- `async upsert(session, package, latest, error)`, `async get_all(session) -> list[row]`.
- alembic 마이그레이션 신규 (`0NN_version_check.py`, 다음 순번).

**B-4. 엔드포인트** (`anygarden/api/v1/system.py`, `machines.py` 컨벤션 미러)
| 메서드 | 경로 | 권한 | 동작 |
|---|---|---|---|
| GET | `/api/v1/system/version` | `forbid_guest` | `{ "version": get_local_version() }`. 외부 호출 없음, 즉시 반환 |
| GET | `/api/v1/system/updates` | `get_admin_identity` | `version_store.get_all` 읽어 `{package, current, latest, update_available, checked_at, error}[]` 반환. **외부 호출 없음** |
| POST | `/api/v1/system/check-updates` | `get_admin_identity` | 대상 패키지별 `fetch_pypi_latest` → `version_store.upsert` → 갱신된 상태 반환 |

### 4.3 프론트엔드 상세
- **Sidebar 푸터** (`components/Sidebar.tsx`): `GET /system/version`으로 서버 버전 표시(모든 유저). admin이면 `GET /system/updates` 캐시를 읽어 update_available 시 `⬆ vX.Y.Z` 힌트.
- **Admin 영역** (기존 admin 네비에 "System" 추가 또는 AdminMachines 근처): 현재/최신/`checked_at` 표 + `[업데이트 확인]` 버튼 → `POST /system/check-updates`. 서버 행 + 각 machine의 `daemon_version`을 `anygarden-machine` 최신과 대조.
- **적용 안내만**: 업데이트 있을 때 실행 대신 명령 문자열 표시 (`pip install -U anygarden` / machine은 `pip install -U anygarden-machine && systemctl --user restart anygarden-machine`).
- 데이터 패칭은 `hooks/useMachines.ts` 패턴 참고한 신규 훅(`useSystemVersion` / `useUpdateStatus`).

## 5. 데이터 흐름

**버전 표시 (①)**: Sidebar mount → `GET /system/version` → 서버가 `importlib.metadata`로 즉시 응답 → 렌더. 외부 의존 없음.

**업데이트 확인 (②, 수동)**: admin이 [업데이트 확인] 클릭 → `POST /system/check-updates` → 서버가 pypi.org 조회 → `version_check` 테이블 upsert → 결과 반환 → UI 갱신. 이후 페이지 로드는 `GET /system/updates`가 **캐시만** 반환.

**수동 → 자동 전환 (후속)**: `scheduler/`에 주기 태스크 추가 → 같은 `fetch_pypi_latest`+`upsert` 호출로 캐시 채움. `GET /updates`·UI·비교 로직 **불변**.

## 6. 에러 처리 / 엣지 케이스

- **PyPI 도달 불가 / air-gap**: `fetch_pypi_latest` → `None`, `version_store`에 `error` 기록. `GET /updates`는 `error` 노출, 배지 숨김, Admin은 "확인 실패(오프라인)" 표기. **절대 요청 블로킹 안 함**.
- **미설치(소스 실행)**: `PackageNotFoundError` → `0.0.0+dev`, 업데이트 배지 억제.
- **버전 비교**: `packaging.version.parse`로 semver 정확 비교. pre/dev 릴리스는 "업데이트 아님"으로 보수적 처리.
- **캐시 없음(최초/재시작 후 자동모드 아님)**: `GET /updates`가 빈/`checked_at=null` 반환 → UI는 "미확인" 표기, admin이 버튼 누르면 채워짐.
- **레이트리밋**: 수동 조회라 자연 제한. 자동 전환 시 스케줄러 간격으로 제어.
- **보안**: check-updates·updates는 admin 전용. 이번 범위는 감지만이라 코드 실행 없음 → 표면은 "서버의 pypi.org outbound GET" 뿐(낮음).

## 7. 테스트 전략

- **단위(백엔드)**:
  - `fetch_pypi_latest` — httpx mock: 200(`info.version`), timeout, 404 → 각각 값/None.
  - `is_update_available` — `packaging` 케이스: 동일/구버전/신버전/None/dev.
  - `get_local_version` — 설치/`PackageNotFoundError` fallback.
  - `version_store` — upsert/get_all 라운드트립.
- **엔드포인트**:
  - `GET /system/version` — 로그인 200, 게스트 차단.
  - `GET /system/updates`·`POST /system/check-updates` — 비admin 403, admin 200. check-updates는 `fetch_pypi_latest` mock으로 캐시 upsert 검증.
- **프론트엔드**: Sidebar 버전 렌더, admin+update_available일 때만 배지 표시, error 시 숨김. `npm run build`(tsc) 통과.
- **회귀**: `uv run pytest packages/cluster`, `packages/machine`(버전 source 변경).

## 8. 구현 영향 파일 (요약)

- 신규: `anygarden/system/version_service.py`, `anygarden/system/version_store.py`, `anygarden/api/v1/system.py`, `db/migrations/versions/0NN_version_check.py`, 프론트 `hooks/useSystemVersion.ts` + Admin System 컴포넌트.
- 수정: `anygarden/__init__.py`, `anygarden_machine/__init__.py`(버전 source), 라우터 등록부, `components/Sidebar.tsx`, `pyproject.toml`(packaging 의존성 확인/추가).

## 9. 미해결 / 후속

- `packaging`이 cluster 의존성에 없으면 추가 필요(구현 시 확인).
- 백그라운드 자동 조회 스케줄러 (후속 이슈).
- agent(`anygarden-agent`) 개별 버전 수집 (필요 시 후속).
