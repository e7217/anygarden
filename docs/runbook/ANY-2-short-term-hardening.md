# ANY-2 cluster/agent 단기 하드닝 운영 노트

## 적용 범위

- scheduler가 선택한 machine으로 `sync_desired_state` 전송 결과를 확인하지
  못하면 이를 미전달이 아닌 `delivery_unknown`으로 기록한다. Placement와
  generation은 유지하며 같은 machine/generation으로만 재조정한다.
- start/recovery 요청은 DB의 만료 가능한 lifecycle lease와 CAS
  (compare-and-swap)로 소유권을 획득한다. 여러 cluster worker가 동시에 처리해도
  한 요청만 generation을 올리고 dispatch한다.
- machine의 actual-state 보고는 현재 placement와 generation이 일치할 때만
  반영한다. Generation 보고 capability를 광고한 daemon의 무버전 보고는
  거절하고, legacy daemon의 무버전 보고도 migration 시점의 호환 epoch가
  지나면 거절한다.
- stop 요청 뒤 늦게 도착한 `running`/`starting` 보고는 `stopping` 상태를
  되돌리지 않는다. stop 중 발생한 `crashed` 보고는 의도한 `stopped`로
  수렴시킨다.
- admin의 unplaced agent 목록은 구조화된 `unavailable_reason`을 우선하며,
  legacy `last_crash_reason`은 현재 상태가 실제 `crashed`일 때만 경고한다.

## 장애 관측

| 신호 | 의미 | 즉시 확인할 항목 |
| --- | --- | --- |
| `lifecycle.no_machine` | engine을 지원하는 online/connected machine이 없음 | machine 연결 상태, engine catalog, capacity |
| `lifecycle.sync_delivery_unknown` | sync frame 전달 여부를 확인할 수 없음; placement는 유지됨 | machine WebSocket flap, 같은 placement의 다음 actual-state report |
| `lifecycle.dispatch_result_fenced` | 늦은 dispatch 결과가 더 새 소유권/generation에 의해 거절됨 | stop/start 또는 다른 worker recovery가 진행됐는지 확인 |
| `lifecycle.start_skipped_inflight` | 중복 start가 process-local 또는 durable lease에서 합쳐짐 | 반복 API 호출 주체; 단발이면 정상 |
| `lifecycle.report_wrong_machine` | 현재 placement가 아닌 machine의 보고 | 재배치 직후 늦은 report 여부 |
| `lifecycle.report_stale_generation` | 이전 process generation의 늦은 보고 | expected/got generation; 반복 시 daemon reconcile 확인 |
| `lifecycle.report_missing_generation` | generation capability를 광고한 daemon이 무버전 보고 | daemon/protocol 버전과 frame 생성 경로 확인 |
| `lifecycle.report_stale_legacy_epoch` | 허용된 legacy epoch 이후 무버전 보고 | daemon 업그레이드 후 versioned report 수신 확인 |
| `lifecycle.report_ignored_during_stop` | stop commit 이전에 생성된 live 보고 | 다음 full report에서 absent/stopped 수렴 여부 |

`lifecycle_dispatch_unknown` activity에는 machine id, generation 및
`same_placement_reconcile` 복구 방식이 남는다. Agent의 구조화된
`spawn_failed` detail은 `delivery_unknown`을 사용한다.

## 복구 절차

1. `no_machine_for_engine`이면 지원 engine을 가진 machine을 online 상태로
   연결한다. machine register 시 orphan placement가 다시 실행된다.
2. `spawn_failed`/`delivery_unknown`이면 기존 placement machine의 WebSocket
   연결을 복구한다. Agent를 unplaced로 바꾸거나 다른 machine에서 시작하지
   않는다. Lease 만료 뒤 start/recovery를 재호출하면 동일 generation을 동일
   machine에 재전송하며, 그 machine의 matching actual-state report가 durable
   acknowledgement가 되어 lease를 해제한다. Lease 기본값은 120초이며 긴급한
   운영 조정이 필요할 때만 `ANYGARDEN_LIFECYCLE_LEASE_SEC`로 변경한다.
3. `report_stale_generation`은 무시된 것이 정상이다. 현재 generation의 다음
   full report가 도착하는지 확인한다. 동일 old generation이 계속 보고되면
   daemon에서 이전 process가 종료되지 않은 상태를 조사한다.
4. stop 후 `stopping`이 유지되면 machine report 주기 한 번을 기다린다.
   full report에서 agent가 사라지면 cluster가 `stopped`로 확정한다. machine이
   offline이면 연결을 복구한 뒤 reconcile한다.

## 경고 해석

- `unavailable_reason`이 있으면 현재 장애 원인의 authoritative source다.
- `last_crash_reason`은 운영 진단 이력으로 유지되므로 pending/start/stop 이후에도
  값이 남을 수 있다. UI는 현재 state가 `crashed`가 아니면 이를 경고로 표시하지
  않는다.
- 의도적 stop 중 process가 crash 형태로 종료돼도 unavailable 경고를 생성하지
  않고 `stopped`로 정규화한다.

## 회귀 검증

```bash
pytest packages/cluster/tests/test_lifecycle.py -q
pytest packages/cluster/tests/test_migrations.py -q
pytest packages/machine/tests/test_daemon.py packages/machine/tests/test_protocol_frames.py -q
npm -w anygarden-frontend test -- --run src/lib/admin-agent-warning.test.ts
npm -w anygarden-frontend run build
```

검증 기준은 lifecycle/DB migration/machine protocol 회귀 테스트와 admin warning,
TypeScript/Vite production build 성공이다.
