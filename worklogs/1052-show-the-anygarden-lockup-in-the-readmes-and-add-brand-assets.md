# docs(brand): show the Anygarden lockup in the READMEs and add brand assets (#1052)

- Commit: `b8e7c57` (b8e7c574bb7bf1254ceb24a7a38d11e536b416a1)
- Author: Changyong Um
- Date: 2026-11-19T11:59:48+09:00
- PR: #1052 (issue)

## Situation

README 세 곳(루트 영문과 한국어, PyPI 페이지로 쓰이는 `packages/cluster/README.md`)은 `# Anygarden` 텍스트 제목만 있었다. 2026-11-13 세션에서 로고 lockup, 마크, 소셜 미리보기 이미지와 이를 만드는 스크립트를 만들었지만 커밋하지 않고 메인 체크아웃에 남겨 두었다.

## Task

- README 제목을 로고로 바꾼다. GitHub 다크 모드에서는 다크 로고를 보여 준다.
- README가 참조하는 이미지와, 이미지를 다시 만들 수 있는 스크립트를 함께 레포에 넣는다.
- PyPI 페이지는 상대 경로를 렌더링하지 못하므로 절대 URL을 써야 한다(#881 결정).

## Action

- `README.md`, `README.ko.md`: `<picture>`의 `prefers-color-scheme: dark` source로 `lockup-dark.png`를 쓰고, 기본은 `lockup.png`, 높이는 56px로 둔다.
- `packages/cluster/README.md`: PyPI는 `<picture>`를 처리하지 않으므로 `<img>` 하나로 `lockup.png`만 쓴다.
- `docs/assets/brand/`에 다음을 추가했다: `lockup.png`, `lockup-dark.png`, `mark.svg`, `social-preview.png`, `social-preview-dark.png`.
- `scripts/brand/generate.mjs`, `prompts.mjs`: 마크, lockup, 파비콘, 배너를 만드는 생성기다.
  - Playwright Chromium으로 PNG와 ICO를 렌더링한다.
  - 색은 `packages/cluster/frontend/src/index.css` 토큰을 따른다.
  - 기본 출력 경로는 `.tmp/brand`다.

## Decisions

- 이미지는 `raw.githubusercontent.com/.../main/...` 절대 URL로 참조한다. 같은 README가 PyPI 설명으로도 쓰이고(#881), PyPI는 레포 상대 경로를 풀지 못하기 때문이다. 대신 이미지가 main에 들어간 뒤에야 GitHub에서도 보인다.
- 생성 스크립트도 커밋했다. 이미지만 넣으면 색이나 문구를 바꿀 때 다시 만들 방법이 없다. 스크립트에는 외부 API 키가 없고, Google Fonts의 Inter만 네트워크로 불러온다.
- 소셜 미리보기 이미지는 아직 어디서도 참조하지 않는다. GitHub 저장소 설정의 Social preview에 수동으로 올릴 용도다(unclear from available context).

## Result

- 병합 뒤 GitHub와 PyPI README 상단에 로고가 보인다. GitHub에서는 다크 모드일 때 다크 로고가 나온다.
- 코드와 테스트에는 영향이 없다.
- 남은 일: GitHub 저장소 Social preview에 `social-preview.png`를 올리는 작업은 수동이다. 파비콘을 앱에 적용할지는 정하지 않았다.
