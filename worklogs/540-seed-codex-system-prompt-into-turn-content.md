# fix(agent): seed codex system_prompt into turn content so identity reaches codex (#540)

- Commit: `d848b43` (d848b43 on fix/540-codex-system-prompt-injection)
- Author: Changyong Um
- Date: 2026-07-14
- PR: #540 (issue) / #541 (PR)

## Situation

#538/#539가 자기정체성을 `_setup_engine`에서 엔진 `system_prompt`에 주입했으나, 재설치+respawn 라이브 실측(2026-07-14)에서 codex-cli 에이전트가 여전히 자기 이름을 "Codex"로 답하는 것이 확인됐다. 원인은 `codex_cli.py`가 `self._system_prompt`을 저장(155)·생성자 전달(477)만 하고 **어디서도 읽지 않는다**는 것: codex `exec`/`resume` 호출에 base-instructions나 `--config`로 system_prompt를 주입하는 경로가 없어 identity 헤더가 codex에 전혀 도달하지 않았다. 반면 화자 라벨(Fix 2/3)은 turn content/ambient 경로라 도달해 정상 작동했다.

## Task

- codex-cli가 system_prompt(identity + base instructions)를 실제로 codex에 전달하게 한다.
- solo 에이전트도 자기 정체성을 알아야 하므로 is_collaborative 무관하게 주입한다.
- codex resume가 히스토리를 보존하므로 매 턴 재-paste하지 않도록 1회 주입 후 억제한다.
- pre-#540 호출부(memory/roster만 쓰는 곳)와 byte-identical 하위호환.

## Action

- `integrations/base.py` `ShaTrackedInjector`: `_system_sha` dict 추가, `apply(...)`에 선택 인자 `system_suffix=""`/`system_label=""` 추가, 방출 순서를 system→memory→roster로. system 블록도 memory/roster와 동일한 sha-delta 로직(첫 방출 라벨 없음, 변경 시 라벨과 재발행).
- `integrations/codex_cli.py`: 턴 prefix 조립의 `self._injector.apply(...)`에 `system_suffix=self._system_prompt or ""`, `system_label="[시스템 지침 업데이트]"`를 **is_collaborative 밖(unconditional)** 으로 전달.
- `tests/test_integrations/test_codex_system_prompt.py`(신규, 5 케이스): injector system 블록(첫 방출·억제·변경 재발행·하위호환) + codex 어댑터 통합(`_call_codex` 스텁으로 첫 턴 prompt에 system_prompt 포함, 2번째 턴 억제 확인).

## Decisions

- **주입 위치 = turn content(ShaTrackedInjector) vs roster suffix vs system_prompt 유지**: roster는 `is_collaborative`일 때만 주입돼 solo 에이전트(실측 대상)에 미도달 → 기각. system_prompt를 codex exec 플래그로 넘기는 방법은 어댑터가 exec 인자를 그렇게 구성하지 않아 큰 변경 → turn content 주입이 최소·확실. ShaTrackedInjector는 "프로세스당 룸별 1회 방출 + 변경 시 재발행 + respawn 시 리셋" 시맨틱을 이미 제공해 그대로 재사용.
- **unconditional 주입**: roster와 달리 자기정체성은 solo에서도 필요(“누구세요?”에 Codex 금지). 그래서 is_collaborative 게이트 밖에 둠.
- **기존 pre-fix 세션 처리**: 인메모리 sha가 respawn 시 리셋되므로 배포 후 첫 턴에 재주입 → 오래된 durable 세션도 identity 획득. resume 히스토리에 이미 있으면 codex 쪽 소폭 중복이나 무해.
- **가정(위반 시 재검토)**: codex resume가 첫 턴에 넣은 system 블록을 히스토리로 유지한다; LLM이 turn-content system 블록을 시스템 지침으로 존중한다(확률적). 라이브 실측으로 확인 예정.

## Result

- 신규 5 케이스 통과, agent 패키지 전체 508 통과, 변경 파일 ruff clean.
- codex-cli가 이제 system_prompt(자기정체성 포함)를 첫 턴 turn content로 주입. SDK 엔진(claude-code/openhands)은 #538 경로로 이미 유효, 본 변경은 codex-cli 갭만 보완.
- 라이브 재측정(재설치+respawn 후 I1 self-name)은 병합 후 수행 예정.
