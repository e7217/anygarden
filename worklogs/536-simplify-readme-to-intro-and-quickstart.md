# docs(readme): 소개 + 시작 방법만 남기도록 README 간소화 (#536)

- Commit: `f461793` (f4617934c0522ea03af6c3d9a42b5c9ded42b382)
- Author: Changyong Um
- Date: 2026-07-14T15:12:18+09:00
- PR: #536

## Situation

루트 `README.md`가 236줄로 비대해져 있었다. 소개·배지에 더해 How It Works
mermaid 다이어그램, Packages 표, 상세 Prerequisites, 66줄 분량의 Ollama 로컬
LLM 게이트웨이 런북, gotchas 표 2개, Support 섹션까지 담아 "랜딩 문서"라기보다
"종합 운영 매뉴얼"에 가까웠다. 처음 방문한 사람이 "무엇인지 + 어떻게 띄우는지"를
1분 안에 파악하기 어려웠고, 상세 내용은 이미 `docs/`·런북·`CONTRIBUTING.md`에
중복 존재했다.

## Task

- README를 간략한 소개 + 시작 방법만 남기도록 축소한다 (목표 40~60줄).
- 상세/복잡한 설명은 삭제하되 정보 손실 없이 기존 문서로 링크 위임한다.
- 시작 경로는 배포판 사용자(PyPI)와 개발자(checkout) 두 청중을 모두 커버한다.
- 남기는 상대 링크는 모두 레포에 실재해야 한다.

## Action

`README.md` 단일 파일 전면 재작성 (24 insertions, 199 deletions):

- **삭제**: Jump-to 내비, Features 6-bullet(→3-bullet로 압축), How It Works
  mermaid 다이어그램, Packages 표, Prerequisites 섹션, `## Run agents on a
  local LLM (Ollama)` 전 섹션(66줄), gotchas 표 2개, `## Support` 섹션.
- **유지·압축**: PyPI/CI/License 배지, 소개 2문장 + 3-bullet 하이라이트,
  `## Quick Start`의 Try it(PyPI 설치→서버 기동→머신 등록) / Develop
  (`make setup && make dev`) 경로, `## Docs` 링크 모음, `## License`.
- **위임**: Ollama 상세는 `docs/runbook/openhands-ollama-setup.md`,
  환경변수는 `.env.example`·`packages/cluster/README.md`, dev 셋업은
  `CONTRIBUTING.md`로 링크.
- 남긴 상대 링크 8개 중 7개는 실재 확인. `DESIGN.md`만 MISS — 단, 이는 원본
  README(line 225)에도 있던 링크로 이번 변경이 만든 회귀가 아니다(아래 Decisions).

## Decisions

- **축소 강도**: "울트라 미니멀(제목+3문장+한 블록)" vs "소개+압축 getting-started"
  중 후자를 선택. 이 프로젝트는 PyPI 배포판이 있는 실사용 도구이고 서버+머신
  2요소를 띄워야 해 "한 줄 실행"으로 환원되지 않는다 → 시작 방법을 살리는 게
  핵심. 계획 문서 `.tmp/plan-readme-simplify.md` §3.2 결정1 참조.
- **시작 경로**: Try it(PyPI)와 Develop(checkout) 둘 다 유지. 청중이 다르고
  압축 비용이 작아 커버리지 이득이 크다.
- **Ollama 섹션 전량 삭제**: 66줄로 단일 최대 덩어리이자 전형적 "복잡한 설명".
  이미 `docs/runbook/openhands-ollama-setup.md` 런북이 존재하므로 링크 한 줄로
  대체 → 삭제가 곧 정보 손실이 아님.
- **DESIGN.md 링크 유지(깨진 링크임에도)**: `DESIGN.md`는 git에 추적되지 않은
  파일이라 GitHub에서 404지만, 원본 README에도 동일하게 있던 링크다. 링크 자체는
  올바르고(의도상 존재해야 하는 파일), 진짜 문제는 "DESIGN.md 미커밋"이라는
  레포 전반의 별개 이슈(CONTRIBUTING.md 등 10곳 이상이 참조). 400줄 디자인 문서를
  이 "README 간소화" PR에 끼워넣는 건 스코프 혼입이라 배제했다.
  - **재검토 트리거**: DESIGN.md를 커밋하는 별도 작업이 이뤄지면 이 링크는
    자동으로 정상화된다. 그 전까지는 pre-existing 깨진 링크로 남는다.
- **배지·License·Contributing 포인터 유지**: 오픈소스 README 관례적 최소 구성,
  각 1~2줄 저비용.

## Result

- `README.md` 236줄 → 60줄. 제거 대상 섹션(mermaid/Packages/Prerequisites/
  Ollama/gotchas/Support) 부재를 grep으로 확인.
- 상대 링크 8개 중 7개 실재 검증 통과. `DESIGN.md`는 pre-existing 미추적 이슈로
  잔존(회귀 아님) — 별도 후속 처리 권고.
- 코드 변경 없음 → 테스트 회귀 무관.
