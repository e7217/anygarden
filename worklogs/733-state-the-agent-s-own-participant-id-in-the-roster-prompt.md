# fix(agent): state the agent's own participant id in the roster prompt (#733)

- Commit: `f1bdf2d` (f1bdf2d6e47d0bfdf559fece3d50e4acfa4e9745)
- Author: Changyong Um
- Date: 2026-09-28T18:40:14+09:00
- PR: #733

## Situation

에이전트는 메시지 원문의 `<@user:{pid}>` 토큰을 그대로 받는다. 그런데 roster suffix(`compose_roster_suffix`)는 handoff 순환을 막으려고 자기 줄을 빼고, identity header(`cli.py`)는 이름만 알려 줬다. 그래서 모델은 자기 participant ID를 몰랐다. 2026-09-28 룸 `9eb1547e…`에서 local-agent는 `<@user:{자기ID}> 참여자 리스트업` 요청을 받고 자기를 목록에서 뺐으며, "그 ID는 명단에 없다"고 답했다.

## Task

- 모델이 자기 멘션을 자기에게 온 것으로 알아보게 한다.
- 참가자 목록 요청에 자기를 포함할 수 있는 정보를 프롬프트에 넣는다.
- handoff/routing 후보(peer 목록)에는 계속 자기를 넣지 않는다.
- 자기 메시지가 자기를 깨우지 않는다는 기존 방어를 유지한다.

## Action

- `packages/agent/anygarden_agent/client.py` `compose_roster_suffix()`
  - roster를 순회하며 `pid in _my_participant_ids`인 항목으로 룸별 자기 줄을 만든다: `You: {name} (id: {pid}). A <@user:{pid}> token in a message addresses you. Never put that token in your own reply.`
  - 이 줄은 `Current room ID` 헤더 바로 아래, peer 목록과 분리해서 둔다. peer 목록 제목은 `Room participants (peers, excluding you).`로 바꿨다.
  - 자기만 있는 룸에서는 헤더와 You 줄만 반환하고, peer 목록과 routing 안내 문단은 생략한다. roster가 비어 있으면 지금처럼 `""`를 반환한다.
  - docstring에 #733을 기록했다.
- `packages/agent/tests/test_roster_self_identity.py`(신규, 6개 테스트): You 줄 내용, peer 목록에서 자기 제외, 자기 토큰 금지 문구, 혼자 있는 룸, 빈 roster, 두 룸의 자기 pid 분리, 자기 멘션이 들어간 자기 메시지가 핸들러를 트리거하지 않음(기존 hard filter 고정).

## Decisions

- 검토한 대안
  - A. suffix에 별도 `You:` 줄 추가 — **채택**
  - B. 모델에 넘기기 전에 자기 토큰을 `@{이름}`으로 치환
  - C. peer 목록 안에 `(you)` 표시로 자기 포함
- A를 고른 이유: 완료 기준 두 가지(자기 멘션 인식, 목록에 자기 포함)가 모두 "모델이 자기 ID를 아는가"로 귀결된다. B는 사용자 content를 바꾸고, 치환 지점이 엔진 어댑터·히스토리 재주입마다 흩어져 있다. 또 목록에 자기를 포함하는 문제는 풀지 못한다.
- C 기각: 프롬프트가 "substituting that peer's id from the list above"라고 peer 목록을 routing 후보로 쓰게 한다. 자기가 섞이면 자기 handoff를 유도할 수 있다.
- `_my_participant_ids`는 룸별이 아니라 전역 set이다. 그래서 자기 pid는 set이 아니라 해당 룸의 roster와 교차해서 정한다.
- 자기 루프 위험: client hard filter, 정책 SKIP, 서버 `rules.py`의 self-mention 제외, delegate 후보 필터라는 기존 4중 방어가 막는다. 가정: 한 룸에서 에이전트의 participant는 하나다. 모델이 금지 문구를 무시하고 자기 토큰을 답변에 쓰는 경우가 관찰되면 B를 후속으로 검토한다.

## Result

- 프롬프트에 룸별 자기 이름·ID와 자기 토큰의 의미가 명시된다.
- 자기만 있는 룸에서도 이제 suffix(room ID + You 줄)가 나간다. 의도된 변경이다.
- `packages/agent` 전체 테스트 663 passed. ruff 오류 수는 main과 같다(기존 13건, 새로 생긴 오류 없음).
- 남은 일: 라이브 노드에서 자기 멘션 질의로 수동 확인.
