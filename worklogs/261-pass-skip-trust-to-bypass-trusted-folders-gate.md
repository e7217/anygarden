# fix(agent/gemini): pass --skip-trust to bypass trusted-folders gate (#261)

- Commit: `25e036f` (25e036fc611d5a971cdf3b0c63822852fa2f8760)
- Author: Changyong Um
- Date: 2026-04-25T15:56:34+09:00
- PR: #261

## Situation

`gemini-cli` 엔진을 사용하는 모든 doorae 에이전트가 사용자 메시지에 응답하지 않는 회귀가 발생. 머신 daemon stdout에는 `gemini.nonzero_exit code=55`가 반복적으로 찍히고 있었다. 직접 재현 결과 cwd를 agent_root(`~/.doorae/agents/<uuid>/`)로 두고 `gemini -p ... --output-format json --approval-mode yolo`를 실행하면 stderr에 "Gemini CLI is not running in a trusted directory" 메시지와 함께 exit 55로 종료, stdout은 비어 있었다. gemini CLI 0.39.x로 업그레이드된 시점부터 도입된 trusted-folders 보안 정책이 원인.

## Task

- agent_root처럼 매 spawn마다 새 UUID로 생성되는 디렉토리에서도 `gemini -p` 비대화형 호출이 정상 동작하도록 만들 것
- 사용자의 글로벌 gemini 설정(`~/.gemini/trustedFolders.json`)을 doorae가 침범하지 않을 것
- 시크릿 주입(`env_with_secrets`) 같은 인접 리팩토링과 스코프가 섞이지 않을 것 — 이번 회귀의 단일 원인만 해결
- 회귀 방지 테스트로 의도가 코드에 박히게 할 것

## Action

- `packages/agent/doorae_agent/integrations/gemini_cli.py:255-261` — `_call_gemini()`의 cmd 리스트에 `--skip-trust` 플래그를 `--approval-mode yolo` 직후에 추가. #261 배경과 함께 5줄 인라인 코멘트로 의도를 박아둠.
- `packages/agent/tests/test_integrations/test_gemini_cli.py:141-163` — `TestCallGemini` 클래스 docstring을 "두 가지" → "세 가지" 보장으로 확장하고 trusted-folders 항목을 추가.
- `packages/agent/tests/test_integrations/test_gemini_cli.py:215-247` — `test_skip_trust_flag_is_passed` 메서드 추가. 기존 `fake_exec` 픽스처 패턴을 재사용해 captured argv에 `--skip-trust`가 포함되는지 검증.
- 검증: 새 테스트 PASS + 같은 클래스의 기존 회귀 테스트(`test_cwd_is_agent_root_and_approval_mode_yolo`)도 PASS, agent 패키지 285개 케이스 통과(OPENAI_API_KEY 환경변수 부재로 인한 1건은 main에서도 동일 실패하는 pre-existing 이슈로 무관).

## Decisions

세 가지 대안을 비교(`.tmp/plan-261-gemini-skip-trust.md` §3.2):

- **A. `--skip-trust` 플래그**: 채택. gemini가 비대화형 자동화 시나리오용으로 공식 제공하는 세션 단위 옵션. cmd argv 한 줄, 글로벌 상태 오염 없음.
- **B. `env=GEMINI_CLI_TRUST_WORKSPACE=true` 전달**: 효과는 동등하나 `env=` 도입은 `secrets.py`의 `env_with_secrets()`와 묶이는 더 큰 변경이라 별도 이슈로 분리하는 게 회귀 추적에 깔끔. 또한 cmd argv가 env보다 가시적이라 향후 누군가 gemini 동작을 조사할 때 의도를 파악하기 쉽다.
- **C. spawn 시점에 `~/.gemini/trustedFolders.json`에 agent_root 등록**: 사용자의 글로벌 gemini 설정을 doorae가 침범하는 부작용. agent destroy/respawn 시 cleanup 책임이 새로 생기고 UUID가 누적되어 파일이 비대해짐.

결정적 근거: gemini `--help`가 `--skip-trust`를 "Trust the current workspace **for this session**"으로 명시. 세션 단위 단발성 trust라 글로벌 흔적 없이 정확히 비대화형 자동화 케이스를 해결.

가정: gemini CLI가 향후 버전에서 `--skip-trust`를 deprecate하지 않는다. 만약 제거되면 env 변수 또는 settings.json 기반 우회로 전환 필요. 재검토 트리거: 업그레이드 후 `--skip-trust` 미인식 에러.

부수적으로 같이 다루지 않은 것: `_call_gemini()`의 `env=` 누락은 OAuth 인증 경로로 현재 사용자 영향이 없어 별도 이슈로 미룸. `import os, signal` 한 줄 ruff E401은 initial commit부터의 pre-existing 경고이며 이번 PR과 무관.

## Result

- agent_root에서 직접 재현: 수정 전 `EXIT=55, STDOUT=""` → 수정 후(동일 환경에서 `--skip-trust` 추가) `EXIT=0, STDOUT=정상 JSON`(`{"response": "안녕하세요. 무엇을 도와드릴까요?", ...}`).
- 단위 테스트 16/16 통과(gemini_cli 어댑터 전체), agent 패키지 285/286 통과(실패 1건은 무관한 OPENAI_API_KEY 환경 의존 이슈).
- 영향 범위: `gemini-cli` 엔진을 쓰는 모든 doorae 에이전트. 다른 어댑터(claude-code, codex)는 동일 정책이 없어 미수정.
- 적용 시점: 머신 재시작 → agent respawn 후부터 새 cmd가 실행됨. 기존 실행 중인 agent는 spawn 시 박힌 cmd를 그대로 쓰므로 자동 적용되지 않는다.
