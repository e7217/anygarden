# fix(dev): post-merge githook·make install/setup이 --all-extras로 sync (#504)

- Commit: `d237707`
- Author: Changyong Um
- Date: 2026-06-24
- PR: #504

## Situation

`.githooks/post-merge`(core.hooksPath=.githooks로 활성)와 `make install`/`setup`이 `uv sync --all-packages`만 실행했다. cluster의 런타임 의존성(fastapi/sqlalchemy/uvicorn 등)은 `anygarden[server]` **extra**라, `--all-extras` 없이는 .venv에 설치되지 않는다. 그 결과 `git pull`/`git merge` 직후 post-merge 훅이 돌 때마다 server 의존성이 빠져 cluster(8001)가 `ModuleNotFoundError: fastapi`로 다운됐다 — #496~#502 작업 중 ff 갱신마다 반복돼 수동으로 `uv sync --all-extras` + cluster reload를 거쳐야 했다.

## Task

- merge/pull 후에도 cluster server 의존성이 유지되도록 자동 sync를 고친다.
- 수동 설치 경로(make)도 동일하게 정합시킨다.

## Action

- `.githooks/post-merge`: `uv sync --all-packages` → `uv sync --all-packages --all-extras` (이유 주석 추가).
- `Makefile`: `install`/`setup` 타깃과 setup의 안내 echo 메시지의 sync 명령을 `--all-extras`로 일괄 변경.

## Decisions

- **`--all-extras` vs `--extra server`** → `--all-extras`. server만 콕 집으려면 패키지별 extra 이름을 나열해야 하고(agent dev 등), 새 extra가 추가될 때마다 훅을 또 고쳐야 한다. `--all-extras`는 모든 optional-dependencies를 깔아 누락이 구조적으로 불가능하다. dev 도구까지 설치되지만 .venv 무게 증가는 무해하고, 메모리에 기록한 운영 함정("`uv sync --all-extras` 필요")과도 일치한다.
- **githook만 vs Makefile까지** → 둘 다. 동일 함정(server extra 누락)이 자동(훅)·수동(make) 경로에 모두 있어, 한쪽만 고치면 다른 경로로 재발한다.
- **자가 적용**: 이 PR이 main에 ff merge되는 순간의 post-merge 훅은 이미 머지된 새 버전(`--all-extras`)으로 실행되므로, 이번 병합부터는 수동 복구가 불필요하다.

## Result

`post-merge` 훅과 `make install`/`setup`이 cluster server extra를 포함해 동기화한다. bash 문법 체크 통과. 이 변경 이후 pull/merge가 cluster를 더 이상 깨뜨리지 않는다. (스크립트 변경이라 단위 테스트 대상 아님 — 동작은 #496~#502에서 `uv sync --all-extras`가 cluster를 복구함으로 이미 입증됐다.)
