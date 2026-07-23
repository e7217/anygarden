# fix(machine): systemd 유닛 PATH에 사용자 설치형 엔진 CLI 경로 포함 (#545)

- Commit: `07d1945` (07d19453a5c5bc1939c0d83b3abaf82fcfa20cc5)
- Author: Changyong Um
- Date: 2026-07-22T15:55:48+09:00
- PR: #545 (issue)

## Situation

anygarden-machine 0.11.0을 systemd user 서비스로 기동하면 엔진이 0개로 탐지됐다. 동일 머신의 로그인 셸에서는 Claude/Codex/Gemini 3개가 모두 정상 탐지되고, 머신 등록·WebSocket 연결도 정상이었다. 즉 사용자 설치 오류가 아니라, 자동 생성된 유닛이 사용자 설치형 CLI를 고려하지 못한 호환성 결함이었다.

## Task

- `install-systemd-unit`이 생성하는 유닛의 PATH가 `{interpreter}:/usr/bin:/bin`으로 고정되어 `~/.local/bin`(claude/codex)·`/usr/local/bin`(gemini)을 빠뜨리는 문제를 해결
- `detector`의 `shutil.which`가 프로세스 PATH를 그대로 쓰므로, systemd·스폰 양쪽 실행 컨텍스트에서 엔진 경로가 보이도록 보정
- 이미 배포된(깨진) 유닛도 재설치 없이 정상화(self-heal)
- 기존에 전무하던 `install_systemd_unit` 테스트 커버리지 확보
- systemd `Environment=`는 `~` 미확장이므로 절대경로 전개 준수

## Action

`packages/machine/anygarden_machine/cli.py`:
- `import os`, `from collections.abc import Iterable, MutableMapping` 추가
- `_wellknown_engine_dirs()` — `[~/.local/bin, /usr/local/bin]`을 `Path.home()`로 절대 전개하는 단일 소스
- `_dedup_path()` — 순서 보존 + 빈 항목/중복 제거 join
- `build_systemd_path()` — `{interpreter bin} + 설치 시점 os.environ["PATH"] + well-known + /usr/bin:/bin`을 dedup 조합. `install_systemd_unit()`의 `Environment=PATH=` 라인이 이 값을 사용하도록 교체
- `ensure_engine_paths()` — 프로세스 PATH에 well-known bin을 멱등 append. `run()` 진입부(config 로드 전)에서 호출 → `detect_engines`의 `shutil.which`와 `spawner.py:996`의 `os.environ.copy()` 양쪽에 전파

`packages/machine/tests/test_cli_systemd.py` (신규, 13건):
- `_dedup_path` 순서/중복/빈입력, `_wellknown_engine_dirs` 구성
- `build_systemd_path` well-known 포함·설치시점 PATH 캡처·무중복
- `ensure_engine_paths` append·멱등·순서보존·기존존재시 무중복·빈PATH
- `install-systemd-unit` CliRunner + temp HOME 유닛 PATH 라인 검증

## Decisions

계획(`.tmp/plan-545-machine-systemd-path.md`)에서 4개 안을 비교:
- **A. 유닛에 고정 2경로 하드코딩** — 최소 변경이나 nvm/asdf/커스텀 prefix 미커버, 배포된 유닛 재설치 필요 → B에 흡수
- **B. 설치 시점 PATH 캡처(채택)** — "install-systemd-unit을 실행하는 셸 = 엔진이 탐지되는 셸"이라는 등식을 이용해 그 PATH를 유닛에 구움 → 탐지 성공 상태와 완전 parity, 임의 설치 위치 커버
- **C. PATH 라인 제거** — systemd user 기본 PATH가 `~/.local/bin`을 신뢰성 있게 포함하지 않아(배포판 편차) 원래 버그로 회귀 → 기각
- **D. 런타임 PATH 보정(채택, 안전망)** — daemon 시작 시 `os.environ["PATH"]` append. 이미 배포된 유닛 self-heal + 스폰 경로 전파 + 실행방식 무관

결정타: B는 새 설치를 근본적으로 올바르게 만들고, D는 B만으로 남는 "이미 깨진 유닛" 갭과 스폰 전파를 덮는다. 둘의 조합이 "새 설치·기존 설치·스폰"을 모두 커버하는 최소 집합이라 B+D를 함께 채택.

가정(위반 시 재검토 트리거): ①`install-systemd-unit`은 엔진이 PATH에 있는 일반 대화형 셸에서 실행된다(sudo/stripped env면 D의 well-known 경로가 최소 보장). ②엔진은 `~/.local/bin` 또는 `/usr/local/bin`에 설치된다(그 외는 B의 캡처 PATH에 의존). 범위 밖으로 남긴 것: 유닛에 PATH를 굽는 대신 `~/.config/environment.d/`에 위임하는 방식(더 큰 배포 정책 변경).

## Result

- machine 패키지 전체 402 passed, 2 skipped (신규 13건 포함, 회귀 없음)
- 수동 검증: `build_systemd_path`가 설치 시점 PATH(`/opt/nvm/...`)를 보존하며 `~/.local/bin`·`/usr/local/bin`을 무중복 병합; CliRunner로 생성한 실제 유닛의 `Environment=PATH=`에 두 사용자 bin 포함, `ExecStart` 절대경로 확인; `ensure_engine_paths`가 축소 PATH에 사용자 bin을 append하는 self-heal 확인
- ruff: 추가 코드 클린(잔존 F541 2건은 `register` 명령의 기존 이슈로 본 변경과 무관, 손대지 않음)
- 미결: 실제 systemd 서비스 기동 후 엔진 3개 탐지 라이브 확인은 배포 환경에서 수행 필요
