# docs: remove stale history, early plans and retired OpenHands runbook (#779)

- Commit: `40d5000`, `29a936a` (chore: ignore local .secret directory)
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

기능 인벤토리를 만들며 문서를 점검한 결과 `docs/`에 현재 코드와 맞지 않는 문서가 남아 있었다. 4월 시점의 상태 스냅샷, 초기 구축 주차별 계획, 그리고 OpenHands 실행이 제거된 뒤에도 남은 Ollama 런북이다. README는 그 퇴역 런북을 현재 설정 안내처럼 링크하고 있었다.

## Task

- 현재 동작을 오해하게 만드는 오래된 문서 제거
- README 링크를 현재 설정 문서로 교체
- 대체된 ADR과 설계 문서는 기록 가치가 있으므로 유지
- 로컬 비밀값 디렉터리(`.secret/`)가 실수로 커밋되지 않게 ignore

## Action

- 삭제: `docs/history/{FINAL,STATUS,SUMMARY}.md`, `docs/plans/week1-server-skeleton.md` ~ `week5-scheduler-e2e.md`, `docs/runbook/openhands-ollama-setup.md`
- `README.md` Docs 섹션: Ollama 런북 링크 → `docs/runbook/direct-model-endpoints.md` ("Direct / self-hosted model endpoints")
- `.gitignore`: `/.secret/` 추가 (별도 커밋)

## Decisions

- 선택지: (1) 오래된 문서에 "historical" 배너만 추가, (2) `docs/archive/`로 이동, (3) 삭제.
- 삭제를 택했다. 해당 문서들은 git 이력에 남아 복구할 수 있고, 배너나 archive 폴더는 검색 결과와 에이전트 문맥에 계속 섞여 현재 동작으로 오인될 수 있다.
- ADR 004·005와 `docs/design/12-llm-gateway.md`는 이미 '대체됨' 표시와 후속 문서 링크가 있어 유지했다. 결정 기록은 결정이 뒤집혀도 남기는 것이 관례다.
- 과거 worklog가 삭제된 파일을 언급하는 부분은 당시 기록이므로 수정하지 않았다.
- 재검토 조건: 이 문서들을 근거로 하는 외부 링크나 위키가 발견되면 리다이렉트용 짧은 안내 문서를 둘지 검토한다.

## Result

- 문서 9개 삭제, README 링크 1개 교체, `.gitignore` 1줄 추가.
- 코드 동작 변화 없음. 테스트 대상 아님.
