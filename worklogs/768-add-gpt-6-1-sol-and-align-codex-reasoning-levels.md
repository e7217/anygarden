# feat(engines): add gpt-6.1-sol and align codex reasoning levels (#767)

- Commit: `d1d1bbb` (d1d1bbb8c578a06ef8496da94d170f5191e49097)
- Author: Changyong Um
- Date: 2026-09-30T19:02:39+09:00
- PR: #768 (issue #767)

## Situation

codex 0.159.0의 서버 모델 목록(`codex debug models`)에 `gpt-6.1-sol`("Latest workhorse model for coding and everyday work")이 추가되었고, 기존 기본 모델 `gpt-6-sol`은 "Previous generation workhorse"로 내려갔습니다. codex-cli 카탈로그에는 새 모델이 없어서 에이전트 생성·설정 화면에서 고를 수 없었습니다. 같은 확인 과정에서 `gpt-5.6-*`와 `gpt-5.5`의 reasoning 레벨이 codex 목록과 어긋난 것도 발견했습니다.

## Task

- `gpt-6.1-sol`을 카탈로그에 추가하고, "workhorse를 기본값으로 쓴다"는 기존 원칙에 따라 기본 모델로 지정
- 이미 `gpt-6-sol`로 고정된 에이전트가 깨지지 않도록 옛 모델은 유지
- 모델별 reasoning 레벨을 codex가 실제로 지원하는 목록에 맞춤 (카탈로그 밖 커스텀 모델 경로는 유지)

## Action

- `packages/cluster/anygarden/engines/catalog.py`: `gpt-6.1-sol`(low~ultra)을 목록 맨 앞에 추가하고 `default_model="gpt-6.1-sol"`로 변경. `gpt-5.6-sol`/`gpt-5.6-terra`는 `minimal`을 빼고 `ultra`를 추가, `gpt-5.6-luna`와 `gpt-5.5`는 `minimal`만 제거. 검증 근거를 주석으로 남김.
- `packages/agent/anygarden_agent/integrations/codex_cli.py`: 모델을 지정하지 않았을 때의 fallback을 `gpt-6.1-sol`로 변경.
- 테스트: `packages/cluster/tests/test_engine_catalog.py`(기본값·순서·레벨·전 모델 `minimal` 거부), `packages/agent/tests/test_integrations/test_codex_cli.py`, `packages/agent/tests/test_room_execution.py`.
- 두 패키지의 `CHANGELOG.md`에 Unreleased 항목 추가.

## Decisions

- **새 모델을 기본값으로 할지, 선택지로만 추가할지**: 카탈로그 주석에 "기본값은 codex가 workhorse라 부르는 티어"라는 원칙이 있고(GPT-5.6 terra → GPT-6 sol), codex가 6.1-sol을 최신 workhorse로 지정했으므로 기본값으로 바꿨습니다. 기존 에이전트의 저장된 모델 값은 건드리지 않습니다.
- **레벨의 기준**: 백엔드 실제 동작과 codex의 모델별 목록 중 codex 목록을 따랐습니다. `gpt-5.6-luna`는 `ultra`로 호출해도 성공했지만(백엔드가 조용히 낮은 레벨로 처리하는 것으로 추정) codex 목록에 없으므로 넣지 않았습니다. 카탈로그 주석에 있던 기존 원칙과 같습니다.
- **`minimal` 제거 근거**: 처음 받은 400 에러는 web_search 도구와 `minimal`을 같이 쓸 수 없다는 내용이었습니다. 그래서 `-c web_search=disabled`로 다시 호출했는데 모델 자체가 거부했습니다(gpt-5.5: "Supported values are: none, low, medium, high, xhigh"). 도구 설정과 상관없이 거부되므로 모델별 목록에서 뺐습니다.
- **엔진 전체 레벨 목록은 유지**: 이 목록은 카탈로그에 없는 커스텀 게이트웨이 모델의 검증에 쓰이고, 그런 모델은 `minimal`을 지원할 수 있어 그대로 두었습니다.
- 다시 검토할 때: codex 목록이나 백엔드 허용 레벨이 또 바뀔 때, 또는 기본 모델 비용·품질 정책이 바뀔 때.

## Result

- 에이전트 생성·설정 화면에서 `gpt-6.1-sol`을 선택할 수 있고, 모델을 지정하지 않은 새 에이전트는 이 모델을 씁니다.
- codex 0.159.0으로 실제 호출 검증: `gpt-6.1-sol`은 low/ultra 모두 성공, `gpt-5.6-sol`/`gpt-5.6-terra`는 ultra 성공, 카탈로그의 모든 구 모델에서 `minimal`은 400으로 거부됨.
- 테스트: cluster의 reasoning·모델 관련 271개, agent 51개 통과.
- 남은 일: 이미 `minimal`로 저장된 `gpt-5.6-*`/`gpt-5.5` 에이전트는 이번 변경과 상관없이 지금도 백엔드에서 거부됩니다. 레벨을 직접 바꿔야 합니다.
