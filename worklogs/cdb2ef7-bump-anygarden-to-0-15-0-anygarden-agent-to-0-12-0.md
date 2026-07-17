# chore(release): bump anygarden to 0.15.0, anygarden-agent to 0.12.0

- Commit: `cdb2ef7`
- Author: Changyong Um
- Date: 2026-07-16
- PR: —

## Situation

GPT-5.6 모델 카탈로그(#543, `bb31ae1`)가 main에 병합됐지만 cluster(`anygarden`
0.14.0)와 agent(`anygarden-agent` 0.11.2) 패키지 버전은 직전 릴리즈(#542)에서 이미
태그·PyPI 배포된 값 그대로였다. `release.yml`은 태그(`{pkg}-v<ver>`) push 시
pyproject 버전과 태그 버전 일치를 검증하므로, 릴리즈하려면 먼저 버전 bump가 필요했다.

## Task

- GPT-5.6 변경이 닿은 두 패키지의 pyproject 버전을 올린다.
- machine은 직전 태그(0.11.0) 이후 변경이 없으므로 제외한다.
- lock/CI가 깨지지 않게 릴리즈 커밋 범위를 #542 관례(pyproject + worklog)에 맞춘다.

## Action

- `packages/cluster/pyproject.toml:7` — `version` 0.14.0 → 0.15.0.
- `packages/agent/pyproject.toml:7` — `version` 0.11.2 → 0.12.0.
- `uv.lock`은 `.gitignore:14`로 tracked가 아니므로 건드리지 않음(#542도 동일).

## Decisions

- **릴리즈 범위**: 직전 태그 이후 cluster·agent 모두 #543(GPT-5.6) 단일 변경만 쌓임,
  machine은 무변경 → machine 제외. #542의 "변경 없는 패키지는 제외" 관례와 동일.
- **버전 증가 폭**: 후보는 (A) 둘 다 minor(0.15.0 / 0.12.0), (B) cluster minor +
  agent patch(0.15.0 / 0.11.3), (C) cluster만 릴리즈. 사용자가 **(A)**를 선택 —
  GPT-5.6 지원이라는 동일 기능 릴리즈로 두 패키지 버전을 정렬하고, #542처럼 두
  패키지를 함께 릴리즈하는 방식. agent 변경(fallback default)이 코드상 작지만 기본
  동작(기본 모델)이 바뀌므로 0.x대에서 minor로 취급.
- **lock 미포함**: `uv.lock`이 gitignored임을 확인 → 릴리즈 커밋은 pyproject만.
  (재검토 트리거: lock을 tracked로 전환하면 release 커밋에 lock 갱신 포함 필요.)

## Result

- 두 pyproject가 0.15.0 / 0.12.0으로 상향. 이 커밋에 태그
  `anygarden-v0.15.0`, `anygarden-agent-v0.12.0`을 달아 push하면 `release.yml`이
  버전 검증 → 빌드(cluster는 SPA 포함) → GitHub Release(자동 노트) → PyPI Trusted
  Publishing 배포를 수행한다.
- 태그 push 전까지는 배포가 일어나지 않음(현재 pending).
