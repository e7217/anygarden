# feat(release): automate PyPI publishing via Trusted Publishing (OIDC) (#402)

- Commit: `8e1c0f6` (8e1c0f6a4b9c2d1e3f5a7b9c0d2e4f6a8b0c2d4e)
- Author: Changyong Um
- Date: 2026-05-29T23:05:18+09:00
- PR: #402 (issue) — PR TBD

## Situation

`release.yml`은 태그 푸시 시 `uv build` → `gh release create`(GitHub Release에 휠/sdist 첨부)까지만 하고 **PyPI 업로드 단계가 없었다.** 그럼에도 PyPI에는 0.8.0이 올라가 있어, 매 릴리스마다 메인테이너가 **로컬에서 수동으로 `uv publish`** 하고 있었다. 이는 0.8.0의 네 번째 결함으로, #397/#398/#400 핫픽스(0.8.1)를 사용자에게 신뢰성 있게 전달하려면 먼저 풀어야 할 전제였다.

## Task

- 태그 푸시만으로 PyPI 게시까지 자동 완료하도록 release.yml 확장.
- 인증은 장기 보관 secret 없는 **Trusted Publishing(OIDC)** 방식.
- 태그 prefix별(anygarden / anygarden-machine / anygarden-agent) 기존 dist_name 매핑·빌드 흐름 유지.
- 게시 절차와 선행 작업(PyPI trusted publisher 등록)을 문서화.

## Action

- `.github/workflows/release.yml`
  - `permissions`에 `id-token: write` 추가(`contents: write` 유지) — OIDC 토큰 교환용.
  - `Create GitHub Release` 스텝 뒤에 `Publish to PyPI` 스텝 추가: `pypa/gh-action-pypi-publish@release/v1`, `packages-dir: dist/`, `skip-existing: true`. `dist/`에는 `uv build --package`가 단일 패키지 산출물만 넣으므로 디렉토리 전체 업로드가 안전.
- `docs/design/08-operations.md`
  - §8.4.5 "릴리스 (PyPI 게시)" 신설: 태그 푸시 자동 게시 흐름, 태그 prefix→패키지 매핑, 락스텝 순서(machine 먼저), trusted publisher 1회 등록 선행 작업, `skip-existing` 재실행 안전성.

## Decisions

`.tmp/plan-402-pypi-trusted-publishing.md`의 의사결정 기록 기반:

- **publish 도구**: `pypa/gh-action-pypi-publish` 채택. 대안 `uv publish --trusted-publishing automatic`(빌드와 동일 툴체인)도 가능하나, PyPA 공식 액션이 OIDC trusted publishing의 레퍼런스 구현이라 토큰 교환·attestations·skip-existing을 최소 설정으로 제공하고 트러블슈팅 자료가 풍부. twine+API 토큰은 사용자가 OIDC를 택해 기각.
- **Environment 게이트**: 없이 repo-level trusted publisher 채택. 현재 1인 메인테이너 락스텝 릴리스라 environment 보호 게이트 이득이 작음. 팀 확장 시 `environment: pypi` + 보호 규칙으로 강화 가능.
- **publish 위치**: 별도 job이 아니라 기존 `build-and-release` job 내 스텝. 산출물이 같은 job의 `dist/`에 있어 artifact 업/다운로드 불필요.
- **가정/미해결**: **PyPI trusted publisher 사전 등록이 필수** — 미등록 상태로 태그를 밀면 publish 스텝 실패. 머지만으로는 게시가 동작하지 않으며, owner가 3개 프로젝트(anygarden/anygarden-machine/anygarden-agent)를 등록해야 첫 0.8.1 태그가 성공한다. PyPI 등록 시 environment를 지정하면 워크플로 `environment:`도 맞춰야 함(등록 시 비우는 것으로 가정).

## Result

- release.yml YAML 검증: `permissions`에 `id-token: write` 포함, 스텝 순서 build(6) → GitHub Release(8) → Publish to PyPI(9), publish 스텝 설정(`packages-dir: dist/`, `skip-existing: true`) 확인.
- 08-operations.md §8.4.5에 게시 런북 추가.
- 코드 변경 없음(CI/문서만) → pytest 회귀 불필요. release.yml은 태그 트리거라 PR CI에서 실행되지 않으므로 정적 검증이 핵심.
- **후속(코드 아님, owner 수동)**: (1) PyPI 3개 프로젝트 trusted publisher 등록, (2) machine→cluster 순서로 0.8.1 태그 푸시, (3) 첫 릴리스 워크플로 모니터링 + 게시 휠 스모크(`pip install "anygarden==0.8.1"`). 등록 전까지 publish 스텝은 실패하므로 머지와 등록 타이밍 조율 필요.
