# fix(machine): mark manifest stopped after request_replacement (#182)

- Commit: `64114e7` (64114e70d1d3a7b9a06ac811fb8d7b6dd7e0b1c7)
- Author: Changyong Um
- Date: 2026-04-19T23:42:58+09:00
- PR: #182

## Situation

`doorae-machine` daemon가 `restart_anywhere` 정책에서 crash budget을 소진하면 서버에 `RequestReplacementFrame`을 보내 재배치 책임을 넘긴다. 그러나 로컬 manifest의 `desired_state`는 `"running"` 그대로 남아 있었다. 같은 블록의 `restart_on_same_machine` 분기는 이미 `update_desired_state("stopped")`를 호출하고 있었는데 `restart_anywhere` 경로만 누락된 대칭 버그였다.

결과: 데몬 재기동 시 `ManifestStore.load_all_running()`이 해당 agent를 다시 실행 목록에 포함 → `_reconcile_agent`가 spawn을 시도 → 서버는 이미 다른 머신에 재배치 완료 → 같은 agent가 두 머신에서 running 상태로 존재하는 split-brain 윈도우. 서버의 다음 `sync_batch`가 교정할 때까지 조용히 반복되는 고장 모드.

## Task

- `_on_agent_crashed`의 `restart_anywhere` + budget 소진 분기에서 `RequestReplacementFrame` 전송 직후 manifest를 `desired_state="stopped"`로 업데이트
- manifest가 이미 지워진 edge case에서 `FileNotFoundError`를 조용히 흡수
- 기존 `restart_on_same_machine` 분기의 동작 회귀 방지
- 핵심 동작을 회귀 테스트로 고정

## Action

- `packages/machine/doorae_machine/daemon.py:418-437` — `restart_anywhere` 분기의 `await self._send(replacement.model_dump())` 직후에 `self._manifest_store.update_desired_state(agent_id, "stopped")` 호출을 추가하고 `try/except FileNotFoundError`로 감쌈
- `packages/machine/tests/test_daemon.py:515-568` — 두 테스트 추가:
  - 기존 `test_crash_budget_exhausted_restart_anywhere`에 `reloaded.desired_state == "stopped"` 검증 추가
  - 신규 `test_request_replacement_survives_missing_manifest` — manifest가 사전에 삭제된 상태에서도 `RequestReplacementFrame`이 정상 전송되는지 확인 (FileNotFoundError 흡수 경로)

## Decisions

`.tmp/plan-182-request-replacement-manifest-cleanup.md`에서 3가지 대안을 비교:

- **A — manifest delete**: 기록 손실. 서버 재push 시 재구성은 문제없지만 감사/디버깅 경로가 없어짐
- **B — `desired_state="stopped"` update** ← 선택. 같은 함수 안에 이미 동일 패턴(427-433줄)이 존재하는 점이 결정적. 대칭 누락이 원인이므로 대칭 복구가 가장 자연스러운 수정이고 side-effect 최소
- **C — 새 상태(`"replaced"`) 도입**: 프로토콜/스키마 확장이 필요해 이슈 범위 초과

가정: 서버가 `request_replacement` 수신 후 새 `sync_desired_state(desired_state="running")`를 이 머신에 다시 보낼 수 있다는 전제. 이 전제가 깨지면(예: 서버가 "replaced"된 agent를 영구적으로 이 머신에서 제외하는 정책 도입) 본 수정은 그대로도 올바르나 의미론을 재확인해야 한다.

## Result

- `uv run pytest` machine 233개 전체 통과
- `fix/182-request-replacement-manifest` 브랜치에 2개 파일 변경 (+63줄)
- 설계 리뷰(2026-04-19)에서 식별된 P0-2 항목 해결. 데몬 재기동 멱등성 확보
- 관련 이슈 #183(per-agent reconcile lock) 머지 후에도 이 분기의 의미론은 유지됨
