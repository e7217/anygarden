# docs(readme): fix first-run steps + add prerequisites & engine setup (#437)

- Commit: `8b75ac5` (8b75ac5cf01fd93eaf57b6c49132e0e02d35ab0e)
- Author: Changyong Um
- Date: 2026-06-14T15:22:20+09:00
- PR: #437

## Situation

루트 `README.md`의 Quick Start는 잘 정리돼 있었으나, 신규 사용자가 그대로 따라 하면
첫 실행에서 막히는 정확성 문제가 코드 대조로 드러났다. 다각도(온보딩·정확성·완성도·구조)
리뷰 후 적대적 검증을 통과한 발견을 코드로 직접 재확인한 결과, Quick Start의 로그인·머신
등록·엔진 식별자 안내가 실제 구현과 어긋나 있었고, 처음 사용자를 위한 사전 요건/엔진
설치·인증/첫 메시지 호출 안내가 빠져 있었다.

## Task

- 첫 실행을 막는 정확성 버그 3건 수정:
  - prod 경로에서 존재하지 않는 admin 계정으로 로그인하라고 안내하는 문구
  - 사전 등록 없이는 즉시 종료되는 `anygarden machine run`만 안내하던 머신 셋업 순서
  - 실제 드롭다운 id(`claude-code`/`gemini-cli`)와 다른 엔진 식별자 표기
- 온보딩 갭 보완: Prerequisites, 엔진 CLI 설치·인증 요건, "첫 메시지 보내기" 단계.
- 제약: 루트 `README.md`만 변경(구조 재편/스크린샷/Contributing은 범위 밖). DESIGN.md 무관(문서).

## Action

`README.md` 한 파일 (+43 / −11):
- **Prerequisites 섹션 신설** (`## Quick Start` 앞): Python 3.11+(agent 런타임 3.12+),
  uv 설치 링크 + `pip install "anygarden[server]"` 대안, 머신 호스트 엔진 CLI, `make dev`용
  Node.js+npm, 지원 OS.
- **Quick Start 본문 재작성**: 서버 기동 → (2) 웹 UI 첫 가입자가 admin이 됨 명시
  (`admin@anygarden.dev`는 dev 모드 전용) → (3) `anygarden machine register` → `machine run`
  2단계 → (4) 룸 생성 후 에이전트 추가 + `@-mention`으로 첫 메시지.
- **엔진 설치/인증 표 추가**: `claude-code`/`codex`/`gemini-cli`/`openhands` 각각 머신 호스트에
  필요한 CLI·API key·import 요건, "데몬 시작 전 설치, 추가 시 재시작" 규칙.
- **Ollama 경로 step 5**에 `@-mention` 첫 메시지 문장 추가.

검증 근거(코드): `packages/cluster/anygarden/app.py:500`(admin 시드는 `if config.dev`),
`packages/cluster/anygarden/auth/routes.py:79`(`is_admin = user_count == 0`),
`packages/machine/anygarden_machine/cli.py:136-144`(토큰 없으면 `sys.exit(1)`) 및
`register` 커맨드가 로그인+감지+등록+토큰저장 일괄 수행,
`packages/machine/anygarden_machine/detector.py:40,42`(`claude-code`→`claude`,
`gemini-cli`→`gemini`), 엔진 어댑터 docstring("must have … installed and authenticated").

## Decisions

- **머신 등록 경로**: "웹 UI Admin → Machines에서 등록" 대신 CLI `anygarden machine register`를
  정식 경로로 채택. register 커맨드가 대화형 로그인→엔진 감지→`/api/v1/machines` 등록→
  토큰 저장을 한 번에 처리해 단계 수가 적고, 이후 `machine run`이 저장된 토큰을 그대로 쓰기
  때문. 웹 UI + `--machine-id/--token` 수동 경로는 동등하게 유효하나 본문 단순화를 위해 생략.
- **엔진 안내를 표 형태로, blockquote 밖에 배치**: GitHub Flavored Markdown은 blockquote 안의
  표 렌더링이 불안정하므로 일반 표로 작성.
- **Python 버전 표기**: 계획 단계의 "3.12+"는 부정확 — 실제 `requires-python`은 cluster/machine
  3.11, agent 3.12. "서버/머신 3.11+, agent 런타임 3.12+"로 정정하고 uv가 인터프리터를
  자동 프로비저닝한다는 점을 덧붙임.
- **범위 한정(B)**: 정확성+온보딩만. Ollama 섹션 축약·env-vars 중복 제거·스크린샷·Contributing은
  구조 변경(C)이라 별도 작업으로 미룸 — 이 가정이 깨지면(예: 같은 PR에서 구조까지 원하면) 재방문.

## Result

- Quick Start를 그대로 따라가도 첫 실행이 가능하도록 정확성 버그 3건 해소, 온보딩 정보 보강.
- 문서 단일 파일 변경으로 코드/테스트 영향 없음(검증 명령 불필요).
- 후속 과제로 남김: Ollama 섹션 runbook 이전, env-vars 노트 중복 정리, 웹 UI 스크린샷,
  Contributing/배지/TOC (이슈 #437 본문의 범위 밖 항목).
