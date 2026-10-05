# docs: stop presenting retired engines as current options (#792)

- Commit: `e7092db`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

기능 인벤토리 조사에서 제거된 기능의 잔재가 cluster 코드에 76곳 있다고 보고됐다(`speaker_strategy`, `orchestrator_agent_id`, `wake_trigger`, `collaboration_mode`). 정리하려고 하나씩 확인해 보니 대부분 잔재가 아니었다. 오히려 사용자가 따라 하는 안내 문서에 퇴역 엔진이 현재 선택지처럼 남아 있었다.

## Task

- 잔재로 보고된 참조를 실제 사용 여부로 분류
- 현재 기능은 남기고, 잘못된 사용 안내만 고치기

## Action

- `packages/agent/README.md`: `--engine` 선택지를 `codex-cli`, `pi-cli`로 고치고 퇴역 엔진 안내 링크 추가
- `packages/agent/anygarden_agent/profile/schema.py`: 엔진 예시 주석 수정
- `docs/design/02~10`: 상단에 "과거 설계 기록(2026-05 기준)" 안내와 현재 문서(ADR-008, 퇴역 엔진 runbook, README) 링크 추가
- 로컬 전용 정리(커밋 없음): 비어 있는 `anygarden/llm_gateway/`(`__pycache__`만 존재), 원본 없는 테스트 `.pyc`

## Decisions

분류 결과:
- `speaker_strategy`, `orchestrator_agent_id`: **현재 기능**이다. 룸 편집 화면에서 `mentioned_only`/`round_robin`/`orchestrator`를 고를 수 있고, WS 핸들러가 전략별로 다르게 라우팅한다. 프로젝트 실행도 총괄 판별에 쓴다. 지우는 것은 제품 결정이라 범위에서 뺐다.
- `wake_trigger`, `collaboration_mode`: 남은 참조는 "제거되었다"는 주석과 제거를 검증하는 테스트다. 유지한다.
- `get_adapter`, `integrate_with_codex_cli`: 프로덕션 경로에서는 쓰지 않는다. 다만 PyPI 배포 패키지의 공개 API(`__all__`)라 제거하면 외부 사용자의 호환성이 깨진다. 얻는 것이 작아 유지한다.
- 이슈 번호를 단 docstring 속 퇴역 엔진 언급: 당시 설계 이유를 설명하는 이력이라 유지한다.
- 설계 문서는 전면 재작성 대신 안내만 붙였다. 재작성은 범위가 크고, 원래 설계 근거를 보존하는 가치도 있다.
- 재검토 조건: 화자 전략을 멘션 전용 정책으로 완전히 대체하기로 결정하면, 그때 UI·WS 라우팅·DB 컬럼 제거를 별도 이슈로 진행한다.

## Result

- 사용자 안내가 실제로 쓸 수 있는 엔진만 가리킨다.
- 코드 동작 변화 없음. agent 테스트 682 passed.
