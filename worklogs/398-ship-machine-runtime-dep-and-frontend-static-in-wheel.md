# fix(release): ship machine runtime dep + frontend static in published wheel (#397, #398)

- Commit: `bd14bd7` (bd14bd7d3a1d9d65b5dd2098c8eed25b6e8e8c98)
- Author: Changyong Um
- Date: 2026-05-29T21:32:01+09:00
- PR: #397, #398

## Situation

게시된 `anygarden` 0.8.0 휠은 클린 환경(`pip install anygarden` / `uvx --from anygarden anygarden-server`)에서 서버를 띄울 수 없었다. 두 가지 별개 원인이 같은 뿌리 — "게시 휠이 필요한 산출물을 담지 못함" — 에서 나왔다. (1) 서버 런타임이 import하는 `anygarden_machine`이 휠 메타데이터에 런타임 의존성으로 없어 `ModuleNotFoundError`로 부팅 실패(#397), (2) 빌드된 SPA 정적 자산(`anygarden/static/`)이 휠에 빠져 `GET /`이 404로 웹 UI 사용 불가(#398).

## Task

- `anygarden-machine`을 dev extras → 런타임 `dependencies`로 옮기고, PyPI 설치가 해결되도록 버전 제약(`>=0.8`)을 명시. `[tool.uv.sources]` workspace 핀이 휠 메타데이터로 누수되지 않음을 보장.
- gitignore된 빌드 산출물 `anygarden/static/`을 sdist·wheel 양쪽에 포함하도록 hatchling 설정 추가. 소스 트리의 `.gitignore`(static은 커밋 금지, CLAUDE.md 규칙)는 유지.
- 릴리스 파이프라인에 frontend 빌드 단계를 `uv build` 앞에, cluster 태그(`anygarden-v*`)에 한해 추가.
- cluster 버전 0.8.1로 범프. 기존 테스트 회귀 없음.

## Action

- `packages/cluster/pyproject.toml`
  - `[project.dependencies]`에 `"anygarden-machine>=0.8"` 추가(주석으로 런타임 import 근거 명시), `[project.optional-dependencies] dev`에서 `anygarden-machine` 제거.
  - `[tool.hatch.build] artifacts = ["anygarden/static/**"]` 추가 — sdist와 wheel 모두에 정적 자산 강제 포함.
  - `[tool.uv.sources] anygarden-machine = { workspace = true }` 유지(로컬 개발용).
  - `version` `0.8.0` → `0.8.1`.
- `.github/workflows/release.yml`
  - `Install uv`와 `Build sdist + wheel` 사이에 `Setup Node`(actions/setup-node@v4, node 20, npm 캐시) + `Build frontend (SPA static assets)`(`npm ci && npm run build`) 단계 추가. 둘 다 `if: steps.parse.outputs.dist_name == 'anygarden'`로 cluster 전용.

## Decisions

`.tmp/plan-397-398-release-wheel-hotfix.md`의 의사결정 기록 기반:

- **#397 의존성 해결 방식**: dev→dependencies 이동(+`>=0.8`) 채택. 대안인 (B) machine의 `secure_chmod` vendoring은 보안 유틸 사본이 어긋나면 조용한 권한 처리 결함이 되어 기각, (C) safefs 공용 패키지 분리는 새 PyPI 패키지+3-way 버전 동기화로 핫픽스 범위 초과라 기각(이슈 #397 본문에서 후속 과제로 명시). 결정적 근거: server/machine/agent가 이미 0.6.0→0.8.0 락스텝 릴리스라 `>=0.8` 제약에 실질 위험이 없음.
- **uv.sources 누수 가정**: workspace 소스 핀이 게시 휠 `Requires-Dist`에 영향 주지 않는다는 가정 — 빌드 후 METADATA를 직접 확인해 검증 완료(`Requires-Dist: anygarden-machine>=0.8`, extra 표시 없음). 향후 uv 동작이 바뀌어 누수되면 이 결정 재검토 필요.
- **#398 정적 자산 포함 방식**: 계획은 `force-include`/`artifacts` 중 미정으로 남겼다. 구현 중 `force-include`(wheel 전용)는 `uv build`가 sdist를 거쳐 wheel을 빌드하는 2단계 경로에서 sdist가 자산을 먼저 떨궈 `Forced include not found`로 실패함을 확인 → sdist·wheel 양쪽에 넣는 `artifacts`로 전환. (B) static을 git 추적 전환은 CLAUDE.md 규칙 정면 위반이라 기각.
- **release.yml 단계 배치**: frontend 빌드는 hatchling이 자산을 집기 위해 `uv build` 앞이어야 하고, machine/agent 릴리스에는 불필요하므로 `dist_name == 'anygarden'` 조건으로 분기.

## Result

- 휠 METADATA에 `anygarden-machine>=0.8`이 런타임 의존성으로 등재(#397 해결). 격리 venv `--no-deps` 설치에서 `anygarden_machine.safefs` import 성공, `pip show anygarden`의 `Requires:`에 machine 포함.
- sdist·wheel 양쪽에 `static/index.html` + assets 5개 포함(#398 해결). 설치된 패키지에서 static 디렉터리·index.html·assets 4개 확인.
- `release.yml` YAML 유효, 단계 순서(Setup Node → Build frontend → Build sdist+wheel) 및 cluster 전용 조건 확인.
- cluster 테스트 993 passed(1 deselected=slow), 회귀 없음. dev extras에서 machine을 빼도 `test_e2e_materialize.py`는 런타임 dep으로 자동 포함되어 정상.
- 미해결: 클린 환경 full-deps 설치 시 `lmnr` vs `openhands-sdk` resolver 충돌이 별도로 존재(이번 변경과 무관, 별도 추적 권장). machine은 코드 변경 없어 0.8.0 유지(cluster `>=0.8`이 허용) — 최초 0.8.1 태그 릴리스 후 실제 아티팩트 재검증 필요.
