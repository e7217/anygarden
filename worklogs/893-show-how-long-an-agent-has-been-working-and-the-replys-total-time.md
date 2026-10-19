# feat(chat): show how long an agent has been working and the reply's total time (#893)

- Commit: `9ee5ae1` (9ee5ae18e8108e2adc33ee69d0bdd6ca85345ef2)
- Author: Changyong Um
- Date: 2026-10-19T12:23:57+09:00
- PR: —

## Situation

에이전트 턴이 진행되는 동안 채팅에는 이름, 단계, 내레이션(#880)만 보이고 경과 시간은 보이지 않았다. 완료된 답변의 "작업 과정" 머리글에도 총 소요 시간이 없었다. #892로 턴의 절대 상한이 없어지면 수십 분짜리 턴이 생기므로, 사용자가 기다릴지 Stop할지 판단하려면 시간이 보여야 했다.

## Task

- 진행 중인 에이전트 옆에 경과 시간을 1초 단위로 보여 준다. 새로고침하거나 방에 늦게 들어와도 0부터 다시 세지 않는다.
- 도구 작업이 있었던 답변의 "작업 과정" 머리글에 총 소요 시간을 보여 준다. 시간이 없는 기존 답변은 그대로 둔다.
- 사람 발신자에게는 시간을 붙이지 않는다(#880의 activity·narration과 같은 규칙).
- 에이전트 프로토콜 변경 없이 동작하고, 구버전 서버·에이전트와 섞여도 깨지지 않는다.
- #892 작업과 충돌하지 않도록 `codex.py`, `pi.py`, `handler_wrapper.py`, `lifecycle.py`, `agents.py`, `OverviewPanel.tsx`는 건드리지 않는다.

## Action

- `packages/cluster/anygarden/orchestration/rules.py` — `TypingTracker`가 참여자별 시작 시각(`since`)을 함께 관리한다. 첫 `is_typing=True` 또는 TTL 만료 뒤 재시작 시 정하고, `False`·만료 시 지운다. `elapsed()`/`elapsed_ms()`를 추가하고 테스트용 `clock` 주입을 받는다.
- `ws/protocol.py`, `agent/protocol/frames.py` — `TypingOut.elapsed_ms: Optional[int]`.
- `ws/handler.py` — 에이전트 typing 방송(`agent_detail`)에만 `elapsed_ms`를 채운다.
- `orchestration/peer_fanin.py`, `app.py` — `dispatch_peer_ask_groups(..., typing_tracker=)`. waiting_peers heartbeat가 caller의 tracker 항목을 갱신해 기다리는 동안에도 시간이 이어지고, wake 시 지운다.
- `agent/runtime/execution/progress_stage.py` — `TraceBuilder`가 생성 시각을 기록하고 `finish()`에 `duration_ms`(int)를 넣는다.
- `messages/metadata.py` — `sanitize_execution_trace`가 `duration_ms`를 int이고 0~7일일 때만 통과시키고, 아니면 그 필드만 버린다.
- 프론트엔드: `lib/elapsed.ts`(`formatElapsed`, `calibrateSince` 1.5초 히스테리시스, `elapsedMsFrom`, `useNow`), `components/Elapsed.tsx`(1초마다 자기만 다시 렌더링), `hooks/useWebSocket.ts`(`typingSince`, forget·방 전환 시 함께 정리), `ChatArea.tsx`(이름 줄 끝에 `· 2m 31s`, 시간은 `shrink-0`·이름은 truncate), `ThreadReplyAffordance.tsx`(가장 오래된 since), `ThreadPanel.tsx`·`ChatPage.tsx`(스레드 작업자 행), `lib/executionTrace.ts`·`ExecutionTrace.tsx`(머리글 `Work · 12m 34s · totals`), en/ko 카탈로그.
- 테스트: pytest(TypingTracker since/elapsed, ws 에이전트 O·사람 X·stop 시 None, peer_fanin waiting 시간 이어짐, TraceBuilder 가짜 시계, sanitize 경계), vitest(`elapsed.test.ts`, `executionTrace.test.ts`, `ChatArea.typing.test.tsx`), Playwright(`agent-work.spec.ts` 4개 시나리오).
- 문서: `docs/requirements.md` ENG-13, `docs/design/01-architecture.md` WS 프레임 표, `packages/cluster/CHANGELOG.md`.

## Decisions

- **시작 시각의 출처: 브라우저 vs 에이전트 vs 서버** — 브라우저가 처음 본 시각은 새로고침하면 0이 된다. 에이전트가 `turn_started_at`을 보내면 프로토콜이 바뀌고 원격 머신 에이전트 업그레이드를 기다려야 한다. 에이전트는 이미 2초 typing과 턴 종료 시 False를 보내므로 서버만으로 턴 경계를 안다. 선택: 서버 `TypingTracker`.
- **전송 형식: `since`(절대) vs `elapsed_ms`(상대)** — 절대 시각은 서버·브라우저 시계 차이만큼 틀어진다. 선택: 상대값을 보내고 브라우저가 프레임마다 재보정. 값이 튀지 않도록 차이가 1.5초 미만이면 기존 since 유지.
- **완료 시간의 출처** — activity log 조인은 메시지 API를 바꾸고 페더레이션 방에는 log가 없을 수 있다. 선택: 에이전트가 측정한 `execution_trace.duration_ms`(trace가 있는 답변에만 붙음, 짧은 대화 답변은 소음 방지를 위해 시간 없음).
- **waiting_peers 중 시간** — caller는 턴을 닫으며 typing을 끄므로 tracker 값이 사라진다. heartbeat가 tracker를 갱신하도록 해 대기 구간 동안 시간이 이어지게 했다(대기 시작부터 셈). 턴 시작부터 이어서 세려면 since를 따로 보존해야 해서 이번에는 하지 않았다.
- **1초 타이머의 렌더링 비용** — ChatArea 전체가 아니라 `<Elapsed>`만 갱신되도록 분리했다.
- 다시 볼 조건: typing 갱신이 TTL(5초)을 넘겨 지연돼 시간이 0으로 돌아가는 일이 관찰되면 since에 별도 유예를 둔다. 같은 방에서 연속 턴 사이에 `sendTyping(False)`가 나가지 않으면 두 턴이 합쳐 보인다.

## Result

- 진행 행이 `planner · running a shell command… · 2m 31s`(ko `2분 31초`)처럼 보이고 1초마다 증가하며, 새로고침 후에도 서버 기준 값으로 이어진다. 시간이 있는 답변 머리글은 `Work · 12m 34s · 4 shell commands`, 없는 답변은 기존과 같다.
- 테스트: cluster 전체 pytest 3952 passed / 7 skipped, vitest 984 passed(133 파일), `npm run build` 통과, `agent-work.spec.ts` 8 passed, `threads.spec.ts`·`right-rail.spec.ts` 통과. `req_report.py` ENG-13 pass(27).
- `chat-width.spec.ts`는 변경 후 단독 실행에서 11/11 통과했지만, 부하(load average ~100) 중 재실행에서는 방 로딩 타임아웃으로 2~4건이 간헐 실패했다. 실패한 테스트는 typing 표시를 다루지 않는다. 변경 전 코드는 한 번 실행해 11/11 통과했다.
- 미확인: 실제 에이전트로 수 분짜리 턴을 돌려 보는 라이브 검증, 스레드 행·스레드 패널 시간 표시의 브라우저 검증, `uv run ruff check` 기준선 자체가 ruff 0.16 기본 규칙으로 이미 실패함(변경 파일의 E/F 오류는 증가 없음).
