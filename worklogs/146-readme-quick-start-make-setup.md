# docs: README Quick Start uses make setup + hook note (#146)

- Commit: `d93852b`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #146 (issue)

## Situation

PR #145로 `make setup` 타겟과 `.githooks/post-merge`를 도입해 `git pull` 후 `.venv/bin/*` 유실을 자동 방지했지만, 루트 `README.md` Quick Start 섹션은 여전히 단순 `uv sync --all-packages`만 안내하고 있었다. 새 컨트리뷰터가 README만 보고 들어오면 hook 활성화를 놓쳐 도입 취지 절반이 사라진다 — 정확히 Issue #142 2차 진단에서 수 시간을 태운 그 상황이 재발할 수 있다.

## Task

- Quick Start를 `make setup`으로 교체해 hook 활성화까지 한 번에
- 왜 중요한지 한 단락으로 설명 (skipping 시 silent uvx fallback 증상 명시)
- 환경 변수 관련 포인터 추가 (`.env.example`, `packages/cluster/README.md` Environment 섹션)
- 기능 변경 없음, 문서만

## Action

- `README.md`
  - Quick Start 코드 블록을 `uv sync --all-packages` → `make setup`으로 교체
  - 실행 명령도 `make -C packages/cluster dev` → `make dev` (루트 Makefile의 `dev` target이 동일하게 cluster로 위임)
  - `make setup`이 무엇을 하는지 + 생략 시의 증상을 한 단락으로 기록
  - 환경 변수 섹션 포인터 추가 (`.env.example`, `packages/cluster/README.md#environment`)

## Decisions

이슈 본문과 커밋 메시지 기반:

**`make setup` vs 기존 `uv sync` 유지**
- A. 기존 `uv sync --all-packages` 유지 + 별도 "Development" 섹션에 hook 안내 — 초기 진입 경로에서 hook을 쉽게 놓침
- B. Quick Start 첫 명령을 `make setup`으로 치환 → **선택**. 가장 많이 읽히는 자리에 가장 중요한 명령을 배치. `make install`은 Makefile에 남아있어 수동 sync가 필요한 경우엔 그대로 사용 가능

결정적 근거: 첫 경험을 설계하는 관점. "hook을 활성화하지 않으면 침묵의 회귀"라는 거친 시나리오를 차단하려면 README의 Quick Start 자체가 hook 포함 경로여야 함. `make setup`을 안 읽는 사용자는 어차피 README 전체를 안 볼 확률이 높음.

**증상 설명 포함 여부**
- A. "hook을 사용하세요" 정도의 간결한 문구
- B. 스킵 시 silent failure의 구체적 증상(`.venv/bin/*` stale + uvx fallback) 명시 → **선택**

결정적 근거: 이 repo에서 실제로 그 증상으로 진단 시간이 수 시간 소요됐다. 남에게 같은 경험을 강요하지 않으려면 "왜 opt-in이 중요한지"를 명시적 텍스트로 박아두는 게 안전. 문서가 길어지는 비용보다 낭비 방지 이득이 큼.

가정: 추후 `Makefile`의 `setup`/`dev` target 이름이 바뀌면 README도 같이 업데이트해야 함. target은 그대로 두되 내용만 바뀔 가능성 높으므로 이 연동 리스크는 낮음.

## Result

- 루트 `README.md` Quick Start가 hook 활성화 경로를 포함
- 신규 clone → `make setup` → `make dev`만으로 개발 환경 구축
- Issue #142 재발 시 `.env.example`/`packages/cluster/README.md#environment` 포인터로 환경 변수 온보딩 시간 단축
- 기능/동작 변경 없음, 테스트 회귀 없음 (문서 전용)
