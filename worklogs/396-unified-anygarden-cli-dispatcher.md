# feat(cli): unified anygarden dispatcher with dependency extras (#396)

- Commit: `70bb0a3` (70bb0a3e228a51e57e1e83f886e98dd2a8a8a9a0)
- Author: Changyong Um
- Date: 2026-05-30T10:30:33+09:00
- PR: #396

## Situation

세 컴포넌트(cluster/machine/agent)가 각자 콘솔 스크립트를 가졌고
(`anygarden-server` / `anygarden-machine` / `anygarden-agent`), 실행 명령어
접두사는 이미 통일돼 있었으나 단일 `anygarden <서브커맨드>` 진입점이 없었다.
또한 서버만 설치명(`anygarden`)과 실행명(`anygarden-server`)이 달라 비대칭이
있었다(#396 원안의 지적). 선행 핫픽스 #397/#398/#400/#402가 모두 머지된
main(`a0e9a6a`) 위에서 진행했다.

## Task

- 단일 `anygarden server|machine|agent|client` 명령어 제공.
- 무거운 의존성을 extra로 게이팅해 역할별 경량 설치 지원
  (`anygarden[server]` / `[machine]` / `[agent]`).
- extra 미설치 시 raw ImportError 대신 설치 안내 출력.
- 구 `anygarden-server` 스크립트는 한 릴리스 동안 deprecation 경고와 함께 유지.
- 패키지 3개·import 경로·PyPI 배포물·trusted publisher는 불변.

## Action

- `packages/cluster/anygarden/cli.py`
  - 얇은 click `dispatch` 그룹 + server/machine/agent/client 서브커맨드 추가.
  - `_load_or_hint(extra, import_fn)`: lazy import 실패(ImportError)를
    `pip install "anygarden[<extra>]"` 안내 + `SystemExit`으로 변환.
  - machine/agent/client는 `anygarden_machine.cli` / `anygarden_agent.cli`를
    호출 시점에 lazy import. server는 같은 모듈 `main`을 재사용하되
    `_server_extra_installed()`가 `importlib.util.find_spec`으로
    fastapi/uvicorn 존재를 선검사해 동일 안내 제공. 각 서브커맨드는 `ctx.args`를
    그대로 위임(passthrough context settings: `ignore_unknown_options`,
    `allow_extra_args`).
  - `deprecated_server_main()`: 구 `anygarden-server` 진입점. stderr에
    deprecation 경고 후 `main()` 위임(`anygarden server` 경로는 미경유).
- `packages/cluster/pyproject.toml`
  - 코어 `dependencies`를 `["click>=8.1"]`로 축소.
  - `[server]`(기존 무거운 스택 + `anygarden-machine>=0.8`), `[machine]`,
    `[agent]` extra 신설. `dev`에 테스트용 server/machine deps 포함.
  - `[project.scripts]`: `anygarden = anygarden.cli:dispatch` 추가,
    `anygarden-server = anygarden.cli:deprecated_server_main`로 별칭 전환.
  - `[tool.uv.sources]`에 `anygarden-agent = { workspace = true }` 추가.
  - version 0.8.1 → 0.9.0 (동작 변경, SemVer minor).
- `packages/cluster/tests/test_dispatch.py` 신규 — 서브커맨드 등록,
  위임(args passthrough), 4개 서브커맨드 미설치 가드 커버(9 tests).
- `packages/cluster/README.md`, `docs/design/08-operations.md` §8.4 — 통합
  CLI + extra 설치 매트릭스 문서화.

## Decisions

`.tmp/plan-396-unified-cli-dispatcher.md` 기반. 사용자와의 장시간 논의에서
B안(디스패처)을 확정하기까지 C1(독립 패키지 유지)·C2(패키지 rename)·
A안(단일 패키지 풀 통합)을 모두 비교했다.

- **B안 채택, A안 기각**: A안(`anygarden_machine.*` → `anygarden.machine.*`
  대량 경로 변경 + 패키지 통폐합)은 거대 diff와 기존 `pip install
  anygarden-machine` 사용자 경로 파괴를 유발. extras 게이팅만으로 동일 목표
  (단일 명령어 + 경량 배포)를 달성할 수 있어 비용 대비 이득이 낮아 기각.
- **코어 구성 = 디스패처만(둘 다 extra)**: server/machine 중 하나를 코어로
  특권화하지 않고 코어를 click만으로. "워커 경량성 + 일관성(PyPA 메타패키지
  관례)"이 결정적. 단점(`pip install anygarden` 단독으로 아무것도 안 뜸)은
  서브커맨드 가드 안내로 해소.
- **위임 = lazy import + click 객체 직접 호출**: subprocess 재실행 대신 같은
  venv의 click 객체를 import해 `args=ctx.args`로 호출. 세 진입점이 모두 import
  가능한 click 객체라 한 줄 위임이 가능하고, 미설치는 ImportError로 자연히
  가드에 연결. subprocess는 PATH 의존·오버헤드·에러 전파 복잡으로 기각.
- **디스패처 위치 = 기존 anygarden(cluster) 패키지**: cli.py top-level이
  click만 import해 디스패처 호스트로 적합. 신규 패키지는 trusted publisher·
  릴리스 파이프라인 재작업을 유발(방금 #402로 정리한 직후라) 기각.
- **machine 의존을 [server] extra에**: `app.py`(서버 런타임)만
  `anygarden_machine.safefs`를 import하므로 machine 의존은 server에 속함.
  코어/디스패처는 machine 없이 동작.
- **가정/미해결**: `pip install anygarden`=서버 흐름이 깨지는 호환성 변경 →
  0.9.0 minor 범프 + 구 별칭 유지 + 문서로 완충. 08-operations.md §8.4
  시나리오의 가설적 예시 명령(`anygarden-server` 등)은 점진 갱신 대상으로 남김
  (범위 통제). machine↔agent는 의존성이 아니라 런타임 프로세스 spawn(uvx 폴백)
  이라 `[machine]` extra가 agent를 포함하지 않음 — 워커는 machine·agent를 각각
  설치(이는 B/C1 공통 속성).

## Result

- 격리 venv 매트릭스 검증: (a) 코어만 → `anygarden --help` 동작 +
  `anygarden machine`이 설치 안내 출력, (b) `anygarden[machine]` →
  `anygarden machine --help` 위임 정상(register/run/status/install-systemd-unit
  노출), (c) server → `anygarden server --help`가 server CLI(옵션 +
  init/migrate)로 위임, `anygarden-server`는 deprecation 경고 출력.
- 휠 METADATA: 코어 `Requires-Dist: click>=8.1`만, Provides-Extra
  server(17 deps)/machine/agent/dev. entry points
  `anygarden = anygarden.cli:dispatch` + `anygarden-server =
  anygarden.cli:deprecated_server_main`.
- `uv run pytest packages/cluster` 1002 passed(기존 993 + 신규 dispatch 9),
  1 deselected(slow). ruff 통과.
- 후속: 0.9.0 릴리스 시 호환성 변경을 릴리스 노트에 명시(`anygarden[server]`
  안내). 08-operations.md 시나리오 예시 명령 점진 갱신. 패키지명 불변이라
  trusted publisher 재등록 불필요.
