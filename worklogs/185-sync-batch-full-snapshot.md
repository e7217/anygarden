# feat(protocol): add is_full_snapshot flag to SyncBatchFrame (#185)

- Commit: `c57b8d8`
- Author: Changyong Um
- Date: 2026-04-20
- PR: #185

## Situation

`SyncBatchFrame`은 "전체 재동기화"와 "부분 업데이트"를 프로토콜상 구분하지 않았다. 머신의 `_handle_sync_batch`는 배치에 없는 local running agent를 **무조건** 고아로 간주해 kill. 서버 측 쿼리 실패·페이지네이션 버그·필터 오류로 빈 배치가 한 번 전송되면 머신이 자신 위의 **전 에이전트**를 정지시키는 단일 고장점이 있었다. K8s List/Watch의 ResourceVersion bookmark 같은 안전장치 없이 암묵적 "full snapshot" 규약에만 의존하던 상태 — 2026-04-19 리뷰 P2-5 항목.

## Task

- `SyncBatchFrame`에 full/partial 스냅샷 구분 플래그 추가
- 기본값을 True로 두어 pre-#185 서버와 호환 유지 (롤아웃 순서 무관)
- 머신의 `_handle_sync_batch`가 `is_full_snapshot=False`면 고아 kill을 스킵
- 서버의 유일한 `send_sync_batch` 지점은 명시적으로 `True`로 marking
- 빈 full snapshot은 기존 의도("의도적 depopulation") 유지, 빈 partial snapshot은 no-op

## Action

- `packages/machine/doorae_machine/protocol/frames.py:67-89` — `SyncBatchFrame`에 `is_full_snapshot: bool = True` 필드 추가, docstring에 두 모드 의미 + 롤아웃 안전성 명시
- `packages/machine/doorae_machine/daemon.py:231-267` — `_handle_sync_batch` body에서 orphan-kill 루프 전체를 `if frame.is_full_snapshot:` 가드로 감쌈. docstring 갱신
- `packages/cluster/doorae/scheduler/lifecycle.py:300-304` — `send_sync_batch`가 보내는 dict에 `"is_full_snapshot": True` 명시. 주석으로 future partial-update call-site는 False 로 보내야 함 명시
- `packages/machine/tests/test_protocol_frames.py:165-197` — 4개 신규 케이스: default True / explicit False / parse without flag (backward-compat) / roundtrip
- `packages/machine/tests/test_daemon.py:TestSyncBatch` — 3개 신규 케이스:
  - `test_partial_batch_does_not_kill_orphans` — 부분 배치에서 untouched agent 보존
  - `test_empty_partial_batch_kills_nothing` — 핵심 회귀 방지: 빈 partial batch는 no-op
  - `test_empty_full_snapshot_kills_all` — 기존 동작 유지: 빈 full snapshot은 depopulation

## Decisions

`.tmp/plan-185-sync-batch-full-snapshot-flag.md`의 대안 비교:

- **A — `is_full_snapshot` 필드 + 기본값 True** ← 선택
- **B — 기본값 False로 보수적 설정**: 기존 서버가 "전체 리셋 의도"를 flag 없이 보낸 케이스에서 머신이 고아를 kill 안 함 → 다른 실패 모드
- **C — 별도 frame 타입(`sync_snapshot` vs `sync_delta`)**: `parse_server_frame` 수정 필요, 표현력 과잉
- **D — server-side generation cursor (K8s ResourceVersion)**: 서버/DB 스키마 변경 필요, 이슈 범위 초과 (필요 시 후속)

결정적 근거: Pydantic `default=True` 필드 추가는 flag 없는 frame도 정상 파싱되고 "full snapshot이 기본"이라는 의미가 현재 코드 실제 동작과 일치. 신규 서버만 `False`를 명시하면 되므로 mixed-version 롤아웃에서 양방향 호환.

가정 / 미해결:
- 현재 서버의 유일한 `sync_batch` 송신부가 `send_sync_batch` 하나뿐이라는 가정 — `rg sync_batch packages/cluster/`로 확인. 향후 target 업데이트용 send site가 추가되면 해당 지점은 반드시 `is_full_snapshot=False`로 보내야 함 (lifecycle.py:300 주석으로 명시)
- 빈 full snapshot 전송을 의도적 depopulation 시그널로 쓰고 있는지 서버 코드 전수 검토는 수행하지 않음. 현재 server는 `select ... where placed_on_machine_id == machine_id`의 결과를 그대로 보내므로 "머신에 할당된 agent가 없음"이 의미하는 건 "kill all local"이 맞음 — 의미론 일관

## Result

- `uv run pytest` (packages/machine) 257개 통과 (기존 250 + 신규 7)
- `uv run pytest` (packages/cluster) 616개 통과, 회귀 없음
- `feat/185-sync-batch-full-snapshot` 브랜치, 5개 파일 변경 (+184/-15)
- 서버 부분 장애(빈 배치)가 머신 단위 대량 장애로 증폭되는 경로 차단
- 후속 확장 가능: delta sync 도입 시 `send_sync_batch_partial(frames)` 같은 메서드 추가 → 대역폭 절감 여지
