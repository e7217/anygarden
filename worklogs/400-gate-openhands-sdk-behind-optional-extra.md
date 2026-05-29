# fix(machine): gate openhands-sdk behind optional extra to unblock clean install (#400)

- Commit: `0bf9e94` (0bf9e94c79f4b1c9d33ca08fc89e3e5b2dccf9d4)
- Author: Changyong Um
- Date: 2026-05-29T22:30:10+09:00
- PR: #400 (issue) — PR TBD

## Situation

#397이 `anygarden-machine`을 cluster(`anygarden`) 휠의 런타임 의존성으로 올린 뒤, 클린 환경 `pip install anygarden`이 `ResolutionImpossible`로 깨지기 시작했다. machine이 `openhands-sdk>=1.21`을 **필수** 런타임 의존성으로 들고 있었고, openhands-sdk가 끌어오는 `lmnr`이 `opentelemetry-semantic-conventions==0.60b1`을 고정 핀하는데 이게 나머지 opentelemetry-instrumentation 스택과 satisfiable한 조합이 없다. `uv sync`는 lock 핀을 써서 통과했기에 #397 머지 시점엔 드러나지 않았고, from-scratch resolve인 `pip install`에서만 터졌다.

## Task

- `openhands-sdk`를 machine 런타임 필수 의존성에서 제거해 클린 설치를 복구.
- OpenHands 엔진을 쓰는 운영자는 명시적으로 opt-in할 수 있게 유지.
- detector의 openhands 탐지 테스트는 계속 동작하도록 dev 경로에 SDK 보존.
- machine 버전 범프(게시 필요).

## Action

- `packages/machine/pyproject.toml`
  - `dependencies`에서 `openhands-sdk>=1.21` 및 #357 주석 제거.
  - `[project.optional-dependencies]`에 `openhands = ["openhands-sdk>=1.21"]` 신설(충돌 배경을 주석으로 기록).
  - `dev` extras에 `openhands-sdk>=1.21` 추가(detector 테스트가 import 경로를 실제로 타도록).
  - `version` `0.8.0` → `0.8.1`.

## Decisions

- **필수 dep 유지 vs extra 분리**: extra 분리 채택. `detector.py:_detect_python_module`이 `importlib.import_module` + `except ImportError: return None`으로 graceful 처리함을 확인 — SDK 부재 시 openhands 엔진만 비노출되고 machine 부팅/바이너리 엔진(claude-code/codex/gemini-cli)은 무영향. 따라서 필수 dep일 이유가 없고, extra로 빼면 충돌만 제거되며 위험이 없다.
- **충돌을 machine에서 해결 vs cluster에서 회피**: machine에서 해결. 근본 원인이 machine의 의존성 선언이므로 cluster에 우회(예: machine을 다시 optional로)를 넣는 것보다 발생원을 고치는 게 맞다. cluster의 #397 런타임 dep 결정(`anygarden-machine>=0.8`)은 그대로 유지(서버는 `safefs.secure_chmod`가 실제로 필요).
- **dev extras에 SDK 잔류**: 빼면 detector openhands 분기가 테스트에서 항상 ImportError 경로만 타게 되어 회귀 탐지력이 떨어짐. 유지로 결정.
- **가정/미해결**: lmnr↔opentelemetry 충돌은 상류(openhands-sdk/lmnr) 사정이라 추후 그쪽이 핀을 풀면 다시 필수 dep으로 되돌릴 수 있음. 그 전까지 extra 게이팅이 안전판. `pip install "anygarden-machine[openhands]"`가 실제로 충돌 없이 풀리는지는 openhands를 쓰는 환경에서 별도 확인 필요(이 변경의 목표는 openhands 미사용 클린 설치 복구).

## Result

- machine 휠 METADATA가 `openhands-sdk`를 `extra == "openhands"`로만 노출(런타임 필수에서 제거).
- 클린 venv에 `anygarden 0.8.1` + `anygarden-machine 0.8.1` with-deps 설치 성공(`pip exit=0`, `ResolutionImpossible` 해소). `import anygarden.app` + `import anygarden_machine.safefs` 정상 → #397 회귀 없음.
- `uv run pytest packages/machine` 245 passed(detector openhands 경로 포함).
- 후속: 0.8.1 릴리스 시 machine·cluster 동반 게시 필요(cluster `>=0.8` 제약이 machine 0.8.1 허용). PR/머지 후 실제 게시 휠 재검증 권장.
