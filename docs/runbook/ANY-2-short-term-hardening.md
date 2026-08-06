# ANY-2 cluster/agent 단기 하드닝 운영 노트

## 적용 범위

- scheduler가 선택한 machine으로 `sync_desired_state` 전송에 실패하면 agent
  placement를 해제하고 `spawn_failed` 구조화 사유를 기록한다.
- 같은 process에서 동시에 들어온 start 요청은 한 번만 dispatch한다.
- machine의 actual-state 보고는 현재 placement와 generation이 일치할 때만
  반영한다.
- stop 요청 뒤 늦게 도착한 `running`/`starting` 보고는 `stopping` 상태를
  되돌리지 않는다. stop 중 발생한 `crashed` 보고는 의도한 `stopped`로
  수렴시킨다.
- admin의 unplaced agent 목록은 구조화된 `unavailable_reason`을 우선하며,
  legacy `last_crash_reason`은 현재 상태가 실제 `crashed`일 때만 경고한다.

## 장애 관측

| 신호 | 의미 | 즉시 확인할 항목 |
| --- | --- | --- |
| `lifecycle.no_machine` | engine을 지원하는 online/connected machine이 없음 | machine 연결 상태, engine catalog, capacity |
| `lifecycle.sync_send_failed` | placement commit 뒤 sync frame 전달 실패 | machine WebSocket flap 및 bus 등록 상태 |
| `lifecycle.start_skipped_inflight` | 중복 start가 기존 start와 합쳐짐 | 반복 API 호출 주체; 단발이면 정상 |
| `lifecycle.report_wrong_machine` | 현재 placement가 아닌 machine의 보고 | 재배치 직후 늦은 report 여부 |
| `lifecycle.report_stale_generation` | 이전 process generation의 늦은 보고 | expected/got generation; 반복 시 daemon reconcile 확인 |
| `lifecycle.report_ignored_during_stop` | stop commit 이전에 생성된 live 보고 | 다음 full report에서 absent/stopped 수렴 여부 |

`agent_unavailable` activity에는 `spawn_failed`, machine id 및
`dispatch_failed` reason이 남는다. Admin UI의 구조화된 경고가 이 기록과
일치하는지 함께 확인한다.

## 복구 절차

1. `no_machine_for_engine`이면 지원 engine을 가진 machine을 online 상태로
   연결한다. machine register 시 orphan placement가 다시 실행된다.
2. `spawn_failed`/`dispatch_failed`이면 대상 machine의 WebSocket 연결을
   복구한다. Agent는 unplaced 상태이므로 admin의 **Retry placement**로 즉시
   재시도할 수 있고, 새 machine register의 orphan recovery 대상에도 포함된다.
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
npm -w anygarden-frontend test -- --run src/lib/admin-agent-warning.test.ts
npm -w anygarden-frontend run build
```

검증 기준은 lifecycle 32개 테스트, admin warning 9개 테스트, TypeScript/Vite
production build 성공이다.
