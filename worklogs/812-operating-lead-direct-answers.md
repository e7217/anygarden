# feat(agent): operating lead answers small requests itself instead of delegating (#812)

- Commits: `f36f06b` (lead workflow, runner direct-answer check, suite v4) and the results commit
- Author: Changyong Um
- Date: 2026-10-12
- Issue: #812

## Situation

벤치마크에서 단순 산출물(DL-01~03)은 team과 solo 모두 3/3이었지만 team이 토큰을 7–40배 썼다. 짧은 글 하나에도 실행 생성 → 위임 → (자발적) QA가 모두 돌았다.

## Task

- 위임이 필요 없는 요청에서 팀 오버헤드를 없앤다
- 위임이 필요한 경우(사용자 요청, 외부 동작, 분업)의 동작은 유지한다

## Action

- `packages/agent/anygarden_agent/integrations/codex_cli.py` `_OPERATING_LEAD_WORKFLOW`: 위임은 서브룸의 권한·도구·분업이 필요할 때만, 혼자 끝낼 수 있는 요청은 직접 답하고, QA는 요청받았거나 외부 동작 직전에만 추가하도록 했다. 기존 지침은 위임하는 방법만 설명했다
- `benchmarks/runner/topology.py` 총괄 지침에서 "요청을 접수하면 실행을 시작해 위임한다"는 강제 문구를 같은 기준으로 바꿨다
- `benchmarks/runner/scenarios.py`: team에서 실행이 없으면 총괄의 직접 답변을 산출물로 인정한다(`_direct_answer`, solo와 공유)
- `suite.json` v4(총괄 지침 변경으로 이전 결과와 직접 비교하지 않는다)
- 테스트: 총괄 지침 문구, team 직접 답변 판정

## Decisions

- **오버헤드를 어디서 줄일지**
  - 채택: 총괄이 위임 여부를 판단하도록 지침에 기준을 준다. 사용자가 "담당자에게 맡겨"라고 하거나 승인된 외부 동작이 필요하면 지금처럼 위임한다
  - 기각: 서버가 요청 크기로 실행 생성을 막는 방식. 요청 크기만으로는 분업·권한 필요 여부를 판단할 수 없다

## Result

- team 21회(DL-01–04, QA-03/06/08, k=3) 모두 통과, 허위 완료 0%
- team/solo 토큰 비율: DL-01 11.4×→0.5×, DL-02 7.0×→0.3×, DL-03 39.9×→1.0×, DL-04 2.7×(향상폭 +100% 유지)
