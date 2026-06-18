# feat(reliability): Wave 1c — re-adopt live agent processes across daemon restart (#451)

- Commit: `4ba11df` (4ba11df493b6772049472c302386ab2fe1750aa2)
- Author: Changyong Um
- Date: 2026-06-18T21:59:14+09:00
- PR: #451

## Situation

ADR-006 Wave 1의 생명주기 복구 조각(machine 패키지). `MachineDaemon`가 재시작되면 메모리 상태(`_running_generations`, `Spawner._agents`)가 비어, 이전에 spawn해 여전히 살아있는 자식 에이전트 프로세스를 인식하지 못했다. 결과: (a) reconcile generation 게이트가 무조건 spawn으로 가 **중복 프로세스(2N)**·중복 응답, (b) 기존 프로세스가 `_agents`에 없어 `kill`이 'not found' → **죽일 수 없는 좀비**, (c) 토큰 재발급 이중 소모. 기존 `ManifestStore.load_all_running`은 죽은 코드였다.

## Task

machine 패키지에 격리, 마이그레이션 없이: 부팅 시 runtime.json + 프로세스 그룹 liveness로 살아있는 에이전트를 re-adopt해 `_running_generations`·`_agents`를 복원하고, kill을 실제 pid/pgid로 재무장. PID 재활용으로 무관 프로세스를 adopt하지 않도록 가드. POSIX 우선(Windows best-effort).

## Action

8 파일 +921/-15 (소스 4 + 테스트 4, 신규 27 테스트).

- `proc_kill.py` — `is_group_alive(pgid)`: POSIX `os.killpg(pgid,0)`(ESRCH→False, EPERM→alive), Windows `psutil.pid_exists` 폴백.
- `manifest_store.py` — `record_runtime`/`load_runtime`/`clear_runtime`/`list_runtimes`: per-agent runtime.json(0o600, {pid,pgid,started_at,engine,generation}), 시크릿 미포함.
- `spawner.py` — `RunningAgent.proc`를 Optional로; `SpawnManifest.generation` 추가; spawn 성공 시 record_runtime(started_at은 psutil create_time, kernel-stable; OSError 비치명); `_cleanup`이 clear_runtime(미추적 에이전트도); `kill`이 proc None 시 proc.returncode/proc.wait 스킵 후 terminate_tree(agent.pid)(pgid==pid); `adopt()`=이중 liveness(is_group_alive + create_time PID-재활용 가드 2s tol) 통과 시 RunningAgent(proc=None) 등록 + `_poll_watch`(is_group_alive 5s 폴링, 종료 시 on_stopped); 실패 시 stale runtime clear. Spawner가 daemon의 ManifestStore 공유(optional 파라미터).
- `daemon.py` — `_readopt_running_agents()`: list_runtimes 순회, 이미 추적 중이면 skip(reconnect idempotent), adopt 성공 시 `_running_generations[agent_id]=generation` 복원. `_connect_and_serve`에서 `_register` 후 / 첫 `_report_actual_state` **전** 호출. spawn manifest에 generation 스탬프.

## Decisions

- **디스크 runtime.json(DB 아님)** — machine은 cluster DB에 직접 접근 안 함(WS 보고만). 로컬 디스크가 진실원이고 기존 0o600 매니페스트 패턴 재사용.
- **부팅 시 _register 후 / 첫 report 전 re-adopt** — reconcile가 generation으로 spawn 결정 → adopt가 먼저 `_running_generations`를 채워야 중복 spawn 억제. **순서가 정확성의 핵심.**
- **PID-재활용 가드 = create_time 비교** — pid 존재만으론 재활용 위험. psutil create_time ≈ 기록된 started_at(2s tol); psutil이 못 찾거나 불일치/AccessDenied면 죽은 것으로(보수적, clear).
- **poll watcher(proc.wait 불가)** — adopt된 프로세스는 데몬의 자식이 아니라 await proc.wait 불가. 5s 폴링으로 종료 감지(지연 ~5s지만 무한 좀비 대비 무해).
- **load_all_running 유지(기각: 대체)** — 기존 테스트가 있어 별도 `list_runtimes` 추가가 저위험.
- 가정: pgid==pid(start_new_session, 확인). Windows는 killpg/setsid 부재 → POSIX 우선, Windows best-effort. runtime.json 기록 실패는 비치명(드문 1회 중복 spawn 허용).

## Result

- machine **373 passed**(+27, 독립 재실행 확인), 2 skipped/35 warnings 모두 기존. ruff 신규 에러 0. 기존 테스트 미변경.
- 효과: 재시작 시 중복 프로세스 2N→0, 좀비 0, 토큰 재발급 N→0. adopt된 proc는 captured stderr 없음(수용).
- 후속: Wave 1d(비용 원장+invocation-block, 기본 OFF), Wave 2.
