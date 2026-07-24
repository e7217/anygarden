# fix(machine): detect install method for self-update (uv tool & pip) (#556)

- Commit: `b9175d5` (b9175d5a4d13d48715661e4b8fea2d54827cddf4)
- Author: Changyong Um
- Date: 2026-07-24T17:06:24+09:00
- PR: #556

## Situation

서버 주도 self-update(#550, WebUI "Update" 버튼)가 프로덕션 머신에서
`No module named pip`으로 실패했다. 해당 머신(ag-machine, anygarden-machine
0.13.0)은 `uv tool install "anygarden[agent,machine]"`로 설치돼 있었는데,
`updater.build_update_command`가 업데이트 도구(pip)와 대상(`anygarden-machine`
단독)을 하드코딩하고 있었다. uv tool이 만든 venv에는 pip이 없고, 그 설치의
엔트리 포인트는 우산 패키지 `anygarden`이라 두 겹으로 어긋났다. self-update는
`install.sh`가 만드는 self-owned venv(pip 포함)만 전제했지만, README가 1순위로
안내하는 설치 경로는 `uv tool`이었다.

## Task

- self-update가 실행 환경의 설치 방식을 판별해 방식별로 올바른
  `(도구 + 대상)` 업데이트 명령을 실행하도록 만든다.
- 흔한 3가지를 1급 지원: self-owned venv-pip(install.sh) / uv tool / pip 통합 venv.
- 판별 실패 시 명확한 "미지원" 에러로 폴백(pipx·conda 등).
- 기존 보안 불변식 유지: 대상 패키지는 상수 집합에서만 선택, PEP 440 버전 검증,
  argv(셸 미사용). 서버 입력이 패키지명/셸이 될 수 없어야 한다.
- 호출부(`daemon._handle_self_update`, `cli.update`)는 변경을 최소화한다.

## Action

- 신규 `packages/machine/anygarden_machine/install_detect.py`:
  - `ResolvedInstall`(frozen dataclass): `method` / `python` / `package` / `index_url`.
  - `resolve_install(manifest)`: manifest 우선 → uv-tool → pip-umbrella → venv-pip →
    미지원 시 `ValueError`. uv-tool 판정을 pip보다 먼저 수행.
  - 헬퍼: `_uv_tool_root`(`UV_TOOL_DIR` → `uv tool dir` → 기본 경로),
    `_is_uv_tool_install`(인터프리터가 uv tool 루트 하위인지), `_has_pip`,
    `_has_distribution`. 상수 `UMBRELLA_PACKAGE="anygarden"`,
    `MACHINE_PACKAGE="anygarden-machine"`, 3개 method 상수.
- `updater.py` 리팩터:
  - `build_update_command(install: ResolvedInstall, target)`로 시그니처 변경
    (manifest → ResolvedInstall). 방식별 분기: `_pip_command`, `_uv_tool_command`.
  - `_uv_tool_command`: `uv tool upgrade anygarden` (버전 핀 시
    `uv tool install anygarden==X --force`), `uv` 미발견 시 `ValueError`.
  - `_SUPPORTED_METHODS`에 `pip-umbrella`, `uv-tool` 추가.
  - `run_update(target, *, install=None, runner=…)`: install 미주입 시
    `resolve_install(load_manifest())` 경유.
- `install_manifest.py`: `method`/`package` 필드 주석을 런타임 감지 맥락으로 갱신.
- `README.md`: 업데이트가 설치 방식을 자동 감지함을 안내(Admin → Machines → Update /
  `anygarden machine update`).
- 테스트: 신규 `tests/test_install_detect.py`(14), `tests/test_updater.py` 갱신(20,
  uv-tool·보안 불변식 포함). machine 패키지 전체 497 passed / 2 skipped.

## Decisions

판별 전략으로 3가지를 놓고 비교했다(계획 `.tmp/plan-556-*.md` §3.2):
- **순수 감지(manifest 폐기)** — install.sh가 manifest에 담는 `index_url`·명시적
  `python`을 재현할 수 없어 사설 인덱스 배포가 깨진다. 기각.
- **manifest 확장(uv tool 설치에도 기록)** — `uv tool install`은 사용자가 직접
  실행해 우리 코드가 개입할 후킹 지점이 없다. 사후 bootstrap 강제는 install.sh
  재설치와 다를 바 없어 "uv tool도 지원" 목표를 무너뜨린다. 기각.
- **manifest 우선 + 감지 폴백(채택)** — 결정적 근거: install.sh 경로는 항상
  manifest를 남기고 uv tool/pip 직접 설치는 절대 남기지 않으므로, **manifest의
  유무 자체가 두 세계를 정확히 가르는 신호**다. 추가 상태 없이 분기 가능.

부수 결정:
- uv tool 감지의 주 신호는 `uv tool dir`(권위 조회), 폴백은 `UV_TOOL_DIR` →
  기본 경로. 애매하면 pip 경로로 내려가거나 "미지원" 에러 — 오판으로 잘못된
  도구를 실행하느니 명확히 실패하는 쪽(보수적 판정).
- uv-tool 판정을 pip보다 우선: uv venv에 `ensurepip`로 pip을 넣은 변종도
  uv 경로로 안전 처리하기 위함.
- 대상 패키지도 방식 종속: uv/pip 통합은 우산 `anygarden`(의존성
  `anygarden-machine`이 함께 올라감), self-owned venv는 `anygarden-machine` 단독.

가정(위반 시 재검토 트리거):
- 프로덕션 머신은 systemd 등 감독 하에 있어 self-exit 후 재기동된다(#550 전제).
- `uv tool upgrade`에 버전 핀 플래그가 없어 pin은 `install --force`로 처리 —
  uv 버전별 문법 차이는 최신 확인 필요. 주 사용 경로(target=None)는 견고.

## Result

- uv tool 설치 머신에서 self-update가 `uv tool upgrade anygarden`으로 동작하게 되어
  `No module named pip` 실패가 해소된다. self-owned venv(install.sh)는 기존
  `pip install -U anygarden-machine` 경로를 그대로 유지(회귀 없음).
- WebUI "Update" 버튼이 설치 방식과 무관하게 동작. 미지원 설치는 명확한 에러 반환.
- 보안 불변식 테스트를 uv-tool 방식으로 확장해 유지 확인.
- 내 변경 파일 ruff clean, machine 전체 497 passed / 2 skipped.
- Pending: uv tool 버전 핀 문법 실환경 검증, pip 통합 설치의 원래 extras 복원
  한계(계획 §6에 명시).
