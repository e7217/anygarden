# feat(agents): 룸별 엔진 세션 매핑을 respawn 너머로 durable화 (#526)

- Commit: `e2443f6`
- Author: Changyong Um
- Date: 2026-07-13T18:01:13+09:00
- PR: —

## Situation

OpenHands 비교 학습에서, anygarden 에이전트가 프로세스 respawn 시 대화 맥락을 잃는다는 결함이 드러났다. 엔진 어댑터는 룸별 resume 핸들(codex `thread_id`, claude `session_id`)을 **인메모리 dict**에만 캐시한다(`codex_cli.py` `_room_thread_ids`, `claude_code.py` `_sessions`). 크래시 재시작·머신 재배치·데몬 재기동으로 프로세스가 새로 뜨면 이 매핑이 사라져, 엔진의 on-disk 세션 스토어가 살아있어도 fresh 대화로 시작한다.

조사 중 두 가지 사실을 확인했다: (1) gemini도 룸 전사를 인메모리로 rebuild해 respawn에 취약하다(계획의 "gemini가 이미 durable" 전제는 부정확). (2) 더 근본적으로, codex가 `.codex/*` 오버레이로 `CODEX_HOME`을 per-agent `.codex`로 리다이렉트한 경우 그 `.codex`(세션 전사본 포함)가 매 materialize마다 prune된다(`spawner.py:215-223`). 즉 "매핑만 저장"은 오버레이 codex엔 무력이고, 완전한 해법은 보안 민감한 prune 변경까지 필요하다.

## Task

- respawn 후에도 룸 대화 연속성을 복원하는 **안전·검증가능한 첫 조각**을 제공한다.
- 엔진의 세션 스토어가 살아있는 경우(오버레이 없는 codex의 host `~/.codex`, claude host store)에 resume을 복원한다.
- 보안 민감하고 라이브 검증이 필요한 "세션 스토어 자체의 respawn 보존"은 별도 범위(part 2)로 명확히 분리한다.

## Action

**agent 패키지 한정, additive, unit-test 가능**한 슬라이스로 구현:
- `integrations/engine_session_store.py` 신설 — `load_sessions(cwd)` / `save_sessions(cwd, mapping)`. 저장 위치는 에이전트 cwd 아래 `.anygarden-engine-sessions.json`. 머신 materializer가 agent-created output을 prune하지 않으므로 respawn을 넘어 생존한다. best-effort: atomic temp+replace 저장, 손상/부재/비-dict → 빈 매핑으로 degrade(기존 인메모리 동작과 동일), 저장 실패는 턴을 죽이지 않는다.
- `codex_cli.py`: `start()`에서 `_room_thread_ids = load_sessions(Path.cwd())`로 복원, `_call_codex` 말미에 `save_sessions`로 저장(set·resume-fail pop 상태 모두 반영).
- `claude_code.py`: `start()`에서 `_sessions` 복원, session_id 승격 지점에서 저장.
- 테스트 `test_engine_session_store.py`: 헬퍼 견고성(roundtrip·부재·손상·비-dict·비-str 필터·temp 잔여 없음) + codex 어댑터의 저장→respawn→복원 end-to-end(unit).
- 회귀 방지: 어댑터가 이제 cwd에 파일을 쓰므로, `test_codex_cli.py`·`test_claude_code.py`에 cwd 격리 autouse 픽스처를 추가해 세션 파일 쓰기가 공유 cwd를 오염시키지 않게 했다(초기 전체 스위트에서 `test_session_resumes_per_room`이 stale 매핑 load로 실패했던 원인).

## Result

- 테스트: agent **487 passed**, ruff clean. 저장→respawn→복원 메커니즘이 unit 레벨로 검증됨.
- 오버레이 없는 codex / claude host-store 케이스에 respawn resume을 복원한다. 오버레이 codex는 스토어가 prune으로 소거되어 resume이 실패(→ 기존 fresh 폴백)하므로 이 슬라이스만으론 효과가 없다 — 안전하게 no-op.
- **잔여(part 2)**: 세션 스토어 자체의 respawn 보존(prune 화이트리스트 세밀화 또는 CODEX_HOME 재배치)은 머신 측·보안 민감(stale-symlink 방어 불변식)이고, respawn resume의 end-to-end 검증은 running cluster+machine+agent+LLM이 필요하다. 별도 사이클(라이브 검증 포함)로 남긴다. 근거는 `.tmp/plan-526-durable-engine-session.md`에 정리되어 있다.
