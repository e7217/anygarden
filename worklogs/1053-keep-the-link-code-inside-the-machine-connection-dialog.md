# fix(machines): keep the link code inside the machine connection dialog (#1053)

- Commit: `a04dc2e` (a04dc2e2ac566aeb88b3d77e2fa180e18bfe5c65)
- Author: Changyong Um
- Date: 2026-11-19T12:01:24+09:00
- PR: #1053 (issue)

## Situation

머신을 등록하면 연결 안내 다이얼로그가 1회용 연결 코드(`agmachine1.…`)를 보여 준다. 이 코드는 공백 없는 긴 문자열이라 줄바꿈되지 않았다. 그래서 다이얼로그 본문 컬럼이 늘어나고 가로 스크롤이 생겼다. 720px 폭에서 1,212px가 넘쳤다.

## Task

- 연결 코드가 다이얼로그 폭 안에 들어오게 한다.
- 셸 명령(설치, 실행)은 줄바꿈되면 읽기 어려우므로 지금처럼 블록 안 가로 스크롤을 유지한다.
- 넘침을 e2e로 고정한다.

## Action

- `packages/cluster/frontend/src/components/MachineConnectionDialog.tsx`
  - `commandBlock(value, name, wrap = false)`: `wrap`이면 `whitespace-pre-wrap break-all`, 아니면 기존 `overflow-x-auto`를 쓴다.
  - 연결 코드 블록만 `wrap`을 넘긴다.
  - 다이얼로그 본문 래퍼에 `min-w-0`을 넣는다. 그리드 자식의 최소 폭이 내용 폭이 되는 것을 막는다.
- `packages/cluster/frontend/e2e/machine-connection.spec.ts`: API를 모킹하고 720px에서 머신을 등록한 뒤, 다이얼로그의 `scrollWidth - clientWidth ≤ 0`을 확인한다.

## Decisions

- 모든 명령 블록을 줄바꿈하는 방법도 있었다. 하지만 `python -m pip install …` 같은 명령이 임의 위치에서 끊기면 읽기 어렵고, 복사할 때 헷갈린다. 그래서 공백 없는 단일 토큰인 연결 코드에만 `break-all`을 적용했다.
- `min-w-0`만으로는 연결 코드 블록 자체가 가로 스크롤 상자가 될 뿐이다. 그러면 복사할 값 전체를 한눈에 보기 어려워, 줄바꿈을 함께 적용했다.
- 다시 검토할 조건: 연결 코드 형식에 공백이나 여러 줄이 생기면 `wrap` 분기가 필요한지 다시 본다.

## Result

- e2e를 추가했다. 수정 전에는 1212px 넘쳐 실패하고, 수정 후에는 통과한다.
- 기존 단위 테스트 13개와 tsc가 통과한다.
- 다른 화면에는 영향이 없다.
