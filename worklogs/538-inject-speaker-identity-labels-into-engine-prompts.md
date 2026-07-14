# fix(agent): inject speaker identity + labels into engine prompts (#538)

- Commit: `030a37c` (030a37c on fix/538-agent-identity-attribution)
- Author: Changyong Um
- Date: 2026-07-14
- PR: #538 (issue)

## Situation

멀티에이전트 룸에서 위임 라우팅·Task 배정은 동작했으나, 라이브 검증 중 에이전트가 화자를 구분하지 못하는 결함이 재현됐다: 자기 이름을 "Codex"(엔진 기본 페르소나)로 답하고, 사람이 보낸 메시지를 자기 것으로 오귀속하며, 상대 발화자를 이름 대신 raw participant_id(`@6973b00a`)로 복창하고, 위임 시 수신 원문을 verbatim 에코했다. 근본 원인은 codex 등 엔진이 보는 프롬프트 텍스트에 화자 신원(이름/역할/자기여부)이 전혀 실리지 않고, 화자 구분이 전적으로 프롬프트 밖 메타 필터(`participant_id`/`_nonce`)에만 의존한 데 있었다.

## Task

- 엔진 프롬프트에 에이전트 자기정체성(이름)을 무조건 주입 — 프로파일 유무·엔진 종류 무관.
- ambient 컨텍스트(breadcrumb)와 직접 수신(addressed) 메시지에 화자 이름·kind 라벨 부여.
- 자기-에코 하드필터 불변식을 회귀 테스트로 고정.
- 제약: sender/roster 인자가 없을 때 pre-#538과 byte-identical(하위호환), 서버 WS 프로토콜 무변경, 위임/턴테이킹 프로토콜(decide_policy·broadcast) 미변경.

## Action

- `cli.py`: `_compose_identity_header`/`_with_identity` 추가, `_setup_engine`의 4개 엔진 분기(claude-code/codex-cli/gemini-cli/openhands) system_prompt에 identity 헤더 병합.
- `coordination/pending_context.py`: `resolve_speaker_label(pid, roster)` 추가(roster hit → `{display_name}({kind})`, miss → None), `format_context_line(msg, roster=None)`로 확장(라벨 or `@{id[:8]}` 폴백).
- `integrations/base.py`: `_room_roster(room_id)` 헬퍼(defensive) 추가, `assemble_user_content(..., sender_participant_id=None)`가 수신 메시지에 발신자 라벨 프리픽스.
- 4개 어댑터(codex_cli/claude_code/gemini_cli/openhands_engine): `ingest_context`의 `format_context_line`에 roster 전달, `on_message`의 `assemble_user_content`에 `sender_participant_id` 전달, claude/openhands의 `_format_context_line` 래퍼도 roster 전달.
- `tests/test_integrations/test_speaker_attribution.py`(신규, 13 케이스): resolve_speaker_label / format_context_line(roster) / assemble_user_content(sender) / identity 헤더.
- `tests/test_client.py`: `TestSelfEchoHardFilter` — 자기 pid 프레임은 핸들러 미도달, peer는 정상 전달.

## Decisions

- **봉쇄(containment) vs 구조 개편** — 관측 이상동작의 대부분이 프롬프트 라벨링으로 무포기 해소 가능한 반면, 구조 개편(out-of-band wake + 결정론적 러너)은 in-room 다중 에이전트 협업(제품 차별점)을 희생하고 "삭제가 아니라 재구축"이라 별도 프로젝트로 분리. 근본 클래스 소멸은 구조만 가능하나 이번 범위는 봉쇄로 한정.
- **정체성 주입 위치 = `_setup_engine` 공통** — 각 어댑터 개별 주입(drift) / 프로파일 강제 기입(빈 프로파일이 이번 버그 원인이라 재발) 대비, 4엔진 단일 관문에서 런타임이 무조건 이름 주입. agent_id는 setup 시점 미확정 가능 → 이름 우선.
- **라벨 게이팅** — sender/roster 인자가 있을 때만 라벨링, 없으면 기존 동작 유지. 기존 `assemble_user_content("r1","hello")=="hello"` 테스트를 깨지 않고 하위호환 확보. addressed 라벨은 roster hit(실제 이름)일 때만 프리픽스해 `@id` 노이즈가 prose에 새지 않게 함.
- **에코 봉쇄 = durable 필터 불변식(테스트) + Fix3** — `broadcast_tailored` sender 제외는 turn-count 리셋(self-echo 의존)을 파손해 기각. Phase 0에서 participant_id가 respawn 너머 안정 + welcome이 replay 선행함을 실증 → 무거운 durable 저장 불필요. 관측된 에코(수신 원문 restatement)는 Fix3(addressed 라벨)가 담당.
- **가정(위반 시 재검토)**: participant_id 안정성, LLM이 프롬프트 라벨을 존중(확률적이라 100% 보장 아님), in-room 협업이 지켜야 할 핵심 가치. rule 4a/ingest_only는 #233 의도적이라 defer.

## Result

- 신규 13 케이스 통과, agent 패키지 전체 503 통과, 변경 파일 ruff clean. 회귀 없음(사전 실패 4건은 cluster 미설치 환경 이슈로 `uv sync --all-packages` 후 통과 확인).
- 프롬프트에 화자 신원·라벨이 실린다는 메커니즘은 단위 테스트로 검증. LLM 행동 개선의 정성 실측(자기이름/오귀속/에코)은 merge 후 재설치+respawn으로 before/after 확인 필요(pending).
- defer: 구조 개편(대화/실행 디커플링), rule 4a 재설계, broadcast sender 제외.
