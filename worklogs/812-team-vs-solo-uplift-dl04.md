# feat(bench): show a team-vs-solo uplift with a governed-submission deliverable (#812)

- Commits: `5504b2e` (DL-04 + submission gate), `a6e2d62` (task role descriptions), `7c686b9` (results)
- Author: Changyong Um
- Date: 2026-10-12
- Issue: #812

## Situation

#811의 첫 코어 측정에서 산출물 시나리오(DL-01~03)는 solo와 team 모두 3/3이었다. team은 토큰을 7–40배 썼고, 팀이 솔로보다 낫다는 근거가 없었다.

## Task

- 팀 구성이 솔로를 실제로 이기는 과제를 스위트에 추가해 측정한다
- 측정 중 드러난 결함은 고친다

## Action

- `benchmarks/runner/scenarios.py`: 산출물 스펙에 `submission.target`을 추가했다. 실행 중 대기 승인을 사용자로서 승인하고, D4 게이트로 mock 수신기에 이 실행의 승인 ID로 정확히 1건의 제출물이 도착했고 필수 사실을 담았는지 확인한다
- `benchmarks/suite.json` v3: DL-04 "작성 → 내 승인 → mock-portal 제출"
- `anygarden/mcp/project_tools.py`: `delegate_project_task`의 `role`에 설명을 추가했다. `release`는 승인된 외부 동작을 실행하는 작업 전용이고, 릴리스 노트를 포함한 문서 작성은 `work`다
- 테스트: 제출 게이트가 이 실행의 영수증만 세는지, DL-04가 수신기와 4번째 게이트를 요구하는지

## Decisions

- **팀의 차별점을 무엇으로 잴지**
  - 채택: 통제된 외부 동작(승인·정확히 1회 제출·감사)을 산출물 판정에 포함한다. 일반 방 단일 에이전트에는 이 경로가 없고, 이것이 실행/승인 체계의 존재 이유다
  - 기각: 더 어려운 글쓰기 과제. DL-03까지 solo가 모두 통과해 글쓰기 품질로는 차이가 나지 않았다

## Result

- 첫 측정: team 0/3. 총괄이 작성 작업을 `role=release`로 분류해 작성자가 완료 보고를 못 했다 → 역할 설명 추가
- 수정 후(codex-cli `gpt-6.1-sol`, k=3): **team 3/3, solo 0/3, 향상폭 +100%**, team/solo 토큰 2.3×. solo는 3회 모두 제출 경로 없음을 정직하게 `blocked`로 보고(허위 제출 0)
- 남은 것(#812): 단순 산출물(DL-01~03)에서 팀 토큰 오버헤드 감소
