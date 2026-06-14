# fix(ci): disable libtmux pytest plugin autoload to unblock collection (#439)

- Commit: `9af1c64` (9af1c64 on branch fix/ci-libtmux-pytest-439)
- Author: Changyong Um
- Date: 2026-06-14
- PR: #439 (issue) — PR number assigned on open

## Situation

CI의 **Test (Linux)** / **Test (Windows native)** 잡이 테스트 수집(collection) 단계에서
즉사했다. PR #438(문서 전용) 진행 중 처음 관측됐고, README 변경과 무관하게 의존성 재해석으로
도는 모든 브랜치(main 포함)에서 동일 재현되는 사전 환경 깨짐이었다. 직전 main CI가 통과했던 건
며칠 전 lock 기준이라, 새 resolve에서 pytest가 8.4+로 올라오며 표면화됐다.

## Task

- CI 테스트 잡이 수집 단계에서 죽지 않도록 복구.
- 제약: 프로젝트가 실제로 쓰는 테스트 동작(pytest-asyncio 등)은 보존, libtmux 미설치 로컬
  환경도 깨지지 않을 것, 세 패키지(machine/agent/cluster) 모두 적용.

## Action

`uv sync --all-extras`가 `openhands-tools`(openhands extra)를 끌어오고, 그 전이 의존성
`libtmux==0.55.1`의 pytest11 플러그인(`libtmux.pytest_plugin`)이 fixture에 `@pytest.mark.skipif`
를 적용하는데, pytest >=8.4가 이를 hard error("Failed: Marks cannot be applied to fixtures")로
처리해 플러그인 import 시점에 세션이 죽었다. libtmux 플러그인은 venv 전역으로 auto-load되므로
세 패키지 잡이 모두 동일하게 영향받는다(`-x`로 machine이 먼저 실패해 나머지는 미실행).

수정 — 세 패키지의 `[tool.pytest.ini_options].addopts`에 `-p no:libtmux` 추가:
- `packages/machine/pyproject.toml:59` — `addopts = "-p no:libtmux"` 신설
- `packages/agent/pyproject.toml:69` — `addopts = "-p no:libtmux"` 신설
- `packages/cluster/pyproject.toml:134` — `"-m 'not slow'"` → `"-m 'not slow' -p no:libtmux"`
각 위치에 원인/근거 주석 첨부.

## Decisions

- **`-p no:libtmux`(플러그인 autoload만 비활성화)** 채택. 대안:
  - pytest를 <8.4로 핀 — 부채 누적·다른 호환성 차단, 기각.
  - libtmux 상향 — openhands-tools 1.21.1이 끄는 전이 의존이라 직접 제어 어렵고 범위 큼, 기각.
  - CI에서 openhands extra 제외 — `--all-extras`로 openhands 엔진 감지를 검증하려는 의도를
    훼손, 기각.
- 결정 근거: 가장 국소적·가역적이며 프로젝트 테스트가 libtmux fixture를 전혀 쓰지 않음(grep 확인).
  플러그인 미설치 환경에서 `-p no:libtmux`는 무해함도 확인.
- CI 명령(`uv run pytest`)에 플래그를 박는 대신 pyproject에 둔 이유: 로컬 `make test`/직접
  `uv run pytest`에도 동일 보호가 적용되도록.
- 가정: libtmux가 fixture-mark 문제를 고친 버전으로 올라오고 lock이 갱신되면 이 옵션은 무의미해질
  뿐 해롭지 않다. 만약 향후 libtmux fixture를 실제로 쓰는 테스트를 도입하면 이 줄을 재검토해야 함.

## Result

- 격리 venv(pytest 9.1.0 + libtmux 0.55.1)에서 수정 전 재현(`Failed: Marks cannot be applied
  to fixtures`) → `-p no:libtmux` 적용 시 정상 수집, 옵션 제거 시 재크래시(회귀 가드) 확인.
- 세 pyproject TOML 파싱·addopts 값 검증 완료.
- 최종 검증은 PR #439의 실제 CI 그린으로 확인(머지 전 watch). 문서 PR #438과 분리해 추적.
