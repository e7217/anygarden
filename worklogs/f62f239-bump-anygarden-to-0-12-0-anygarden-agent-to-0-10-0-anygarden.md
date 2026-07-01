# chore(release): bump anygarden to 0.12.0, anygarden-agent to 0.10.0, anygarden-machine to 0.10.0

- Commit: `f62f239` (f62f23911a1a00afb3612fd8929f01a68244edf5)
- Author: Changyong Um
- Date: 2026-07-01T23:09:04+09:00
- PR: —

## Situation

직전 릴리스(anygarden 0.11.0 / anygarden-agent 0.9.1, 2026-06-23; anygarden-machine 0.9.0) 이후 main에 9개 커밋(#490~#507)이 병합됐지만 버전 bump·태그·PyPI 게시가 없어 세 패키지의 PyPI 최신본이 main보다 뒤처져 있었다. 특히 codex 엔진 재편(codex-cli exec 어댑터 도입 → SDK codex 완전 제거)이 통째로 미배포 상태라 `pip install anygarden-agent`는 여전히 구 SDK codex 기반 0.9.1을 받았다.

## Task

- 세 패키지의 `pyproject.toml` version을 릴리스 대상 버전으로 상향한다.
  - `anygarden-agent`: 0.9.1 → 0.10.0 (codex 엔진 재편으로 변경 폭이 커 minor)
  - `anygarden-machine`: 0.9.0 → 0.10.0 (codex-cli spawn/detector 지원, 턴 타임아웃 env)
  - `anygarden` (cluster): 0.11.0 → 0.12.0 (per-agent 턴 타임아웃 DB·API·UI, codex-cli catalog/템플릿, MCP 202 ack)
- 릴리스 워크플로(`.github/workflows/release.yml`)의 `Verify pyproject version matches tag` 스텝을 통과하도록 태그 버전과 정확히 일치시켜야 한다.

## Action

- `packages/agent/pyproject.toml:7` — `version = "0.9.1"` → `"0.10.0"`
- `packages/machine/pyproject.toml:7` — `version = "0.9.0"` → `"0.10.0"`
- `packages/cluster/pyproject.toml:7` — `version = "0.11.0"` → `"0.12.0"`

버전 라인 3줄만 변경(직전 릴리스 커밋 6730efc와 동일한 최소 변경 패턴). cluster의 `[server/machine/agent]` extra 의존성 floor(`anygarden-machine>=0.8`, `anygarden-agent>=0.8`)는 프로젝트 관례대로 건드리지 않음 — 설치 시 latest가 해소되고, 여러 릴리스에 걸쳐 loose floor를 유지해 왔다.

## Decisions

- **버전 스킴: minor 일괄 (agent/machine 0.10.0, cluster 0.12.0)** vs agent만 minor·나머지 patch.
  - agent는 codex 엔진을 통째로 재작성(SDK codex 제거 + codex-cli 도입 + DB 마이그레이션 050으로 기존 `codex` 엔진 → `codex-cli` 이관)해 사실상 breaking에 가까워 minor가 명확.
  - machine·cluster도 additive feature(codex-cli 지원, per-agent 턴 타임아웃)를 담고 있어 patch보다 minor가 의도를 더 정확히 반영. 세 패키지를 0.10/0.10/0.12로 맞추면 codex-cli 세대가 lockstep으로 묶여 "cluster 0.12 + agent 0.9.1(codex-cli 없음)" 같은 어긋난 조합의 혼선을 줄인다.
  - 0.x 라인이라 minor로 feature/breaking을 함께 신호. semver major bump은 1.0 이전이라 부적절.
- **의존성 floor 미상향**: cluster 0.12는 codex-cli 세대 agent/machine과 함께 동작하도록 설계됐지만, floor를 `>=0.10`으로 올리는 것은 프로젝트의 loose-floor 관례(6730efc 등 과거 릴리스가 version 라인만 변경)와 어긋난다. codex-cli 부재는 import crash가 아닌 soft degradation이고 설치 시 latest가 잡히므로 `>=0.8` 유지. (이 가정이 깨져 하드 비호환이 생기면 floor 상향을 재검토해야 함.)
- **릴리스 순서(락스텝)**: 태그 푸시 시 machine → agent → cluster 순. runbook §8.4.5대로 cluster(서버)가 `anygarden-machine`을 런타임 의존하므로 machine을 먼저(또는 동시에) 게시.

## Result

세 패키지 version이 0.10.0/0.10.0/0.12.0으로 정렬됨. 이 커밋을 PR로 main에 squash-merge한 뒤 `anygarden-machine-v0.10.0`, `anygarden-agent-v0.10.0`, `anygarden-v0.12.0` 태그를 밀면 `release.yml`이 빌드 → GitHub Release → PyPI Trusted Publishing 게시를 수행한다. 게시 후 `pip install anygarden-agent`가 codex-cli 세대(0.10.0)를 받게 된다. 태그 푸시/게시 결과는 후속 단계에서 확인 예정.
