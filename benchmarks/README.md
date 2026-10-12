# Anygarden 벤치마크

Anygarden의 성능은 엔진(모델) 점수가 아니라 **같은 엔진을 팀으로 묶었을 때 더 많이, 더 정직하게, 감당할 비용으로 끝내는가**로 잰다.
이 디렉터리는 그 측정을 반복할 수 있게 만든 도구와 기록이다.

## 세 층과 역할

| 층 | 무엇을 보장하나 | 어디서 | 언제 |
|---|---|---|---|
| 결정적 테스트 | 기능이 깨지지 않는다. `docs/requirements.md`의 모든 요구사항이 선언한 계층의 테스트로 검증된다 | pytest `@pytest.mark.req`, vitest·Playwright 제목의 `[ID]` | 모든 PR (CI `Requirement coverage` 잡) |
| 라이브 벤치마크 | 실제 엔진으로 협업이 끝까지 동작한다. pass^k, 허위 완료율, 성공당 비용, 팀 대비 솔로 향상폭 | `benchmarks/runner` + `benchmarks/suite.json` | 큰 기능 머지 후, 릴리스 전 |
| 운영 지표 | 실제 사용에서 응답 유실·멈춘 턴·완료율·비용이 어떤지 | `GET /api/v1/metrics/collaboration` (admin) | 상시 |

라이브에서 한 번 발견한 제품 결함은 결정적 테스트로 옮긴다(`verify-scenarios` 스킬의 `regression-candidates.md`).
운영 지표에서 이상이 보이면 시나리오로 만든다. 이 순환이 시나리오 세트를 실제 사용에 가깝게 유지한다.

## 요구사항 커버리지

- 카탈로그: `docs/requirements.md` — `| ID | 요구 | 검증 |`. 검증은 `pytest`, `vitest`, `e2e`, `build`, `gap` 중 하나 이상.
- 태그: pytest는 `@pytest.mark.req("EXE-09")`, vitest·Playwright는 테스트 제목 끝에 `[EXE-09]`.
- 게이트: `scripts/req_report.py junit/*.xml --spec docs/requirements.md --require-coverage`
  - 선언한 테스트 계층에 통과한 테스트가 없으면 실패
  - 카탈로그에 없는 ID를 태그한 테스트가 있으면 실패
  - `gap`은 허용하되 매 실행마다 "Known gaps"로 출력한다. 구현하면 `gap`을 실제 계층으로 바꾼다.
- 로컬: `make req-report` (cluster·agent pytest를 JUnit으로 돌린 뒤 보고)

## 라이브 벤치마크 실행

격리 노드에서만 돌린다. 개발 서버(5173/8001)나 `~/.anygarden`은 건드리지 않는다.

```bash
RUN=.tmp/bench/node-$(date +%Y%m%d-%H%M)
# 승인 시나리오(QA-08)용 mock 제출 대상 — 러너가 8189에서 직접 띄운다
export ANYGARDEN_PROJECT_ACTION_TARGETS="$(uv run python -m benchmarks.runner targets)"
.venv/bin/anygarden start --data-dir $RUN --host 127.0.0.1 --port 8002 &

uv run python -m benchmarks.runner s0 --model gpt-6.1-sol            # 스택 게이트
uv run python -m benchmarks.runner run --mode both --k 3 --model gpt-6.1-sol
uv run python -m benchmarks.runner report                            # 추세 리포트
```

- `run`은 토폴로지마다 S0을 먼저 돌리고, 실패하면 아무것도 기록하지 않고 멈춘다.
- 매 실행은 새 프로젝트를 만든다. 실행 끝에 그 프로젝트로 한정한 협업 지표(`metrics`)를 함께 기록하고 에이전트를 멈춘다.
- 결과 파일 `benchmarks/results/<UTC시각>-<커밋>.json`은 **커밋한다**. 원본 증거(대화, 턴, 태스크)는 `.tmp/bench/<시각>/`에 남고 커밋하지 않는다.
- 비용: 시나리오 수 × 모드 × k만큼 모델을 호출한다. 실행 전에 규모를 확인한다(코어 세트 both·k=3 ≈ 30회 실행).

### 판정 주석

실행 결과는 기계 판정(`pass`/`fail`/`error`)이다. 독립 판정자가 증거를 보고 바로잡을 수 있다.

```bash
uv run python -m benchmarks.runner annotate benchmarks/results/<file>.json GT-03 solo 2 environment "샌드박스가 파일 쓰기를 막음"
```

- `invalid`(계획 결함)·`environment`(호스트 제약)·`error`(드라이버 오류)는 pass^k 계산에서 빠진다.
- 남은 실행이 k보다 적으면 `incomplete`다. 절대 `pass`로 세지 않는다.

## 지표 정의

| 지표 | 정의 |
|---|---|
| pass^k | 집계 대상 실행이 k개 이상이고 모두 통과 |
| 허위 완료율 | 시스템이 완료(`done`)라고 보고한 실행 중 하드 게이트를 통과하지 못한 비율 |
| 성공당 비용 | 집계 대상 실행의 보고 비용 합 ÷ 통과 수 (비용을 보고하지 않는 엔진은 0으로 들어가므로 `requests_without_cost`와 함께 본다) |
| 팀 대비 솔로 향상폭 | 같은 산출물 시나리오에서 team 통과율 − solo 통과율, 실행당 비용 비율 |
| 응답 유실률 | 완료됐지만 응답도 의도적 skip도 없는 턴 ÷ (응답한 턴 + 유실 턴) |
| 멈춘 턴 | 열린 상태로 15분 넘게 갱신이 없는 턴 |

## 시나리오 세트

`suite.json`이 코어 세트다. 시나리오를 바꾸면 `version`을 올린다. 버전이 다른 결과끼리는 추세를 비교하지 않는다.

| ID | 모드 | 확인하는 것 |
|---|---|---|
| QA-03 | team | 총괄이 하위 방 담당자에게 맥락과 함께 위임하고 담당자가 깨어난다 |
| QA-06 | team | 담당자가 추측 대신 사용자에게 날짜를 묻고, 답이 작업에 반영돼 완료된다 |
| QA-08 | team | 외부 제출은 승인 전 0회, 승인 후 정확히 1회, 거절 시 0회 |
| GT-01 | solo | 도구가 필요한 요청이 작업으로 승격되고 에이전트가 스스로 종결 상태를 보고한다 |
| GT-02 | solo | 단순 질답은 작업을 만들지 않는다 |
| GT-03 | solo | 질문 → 스레드 답변 → 같은 작업이 한 번만 재개된다 |
| DL-01–DL-04 | solo, team | 같은 요청을 혼자와 팀으로 처리해 산출물의 필수 사실·금지 사실을 비교한다 (향상폭·허위 완료) |

프로토콜 시나리오(`QA-*`, `GT-*`)는 `runner/scenarios.py`의 코드다. 산출물 시나리오(`DL-*`)는 `suite.json`의 데이터다.

### 측정 기록

- 2026-10-12 첫 코어 측정(codex-cli `gpt-6.1-sol`, k=3, `results/20261012-*.json`): 10개 시나리오 모두 pass^3, 허위 완료 0%, 30회 실행 동안 응답 유실·멈춘 턴·실패 턴 0.
  - GT-03은 이 호스트에서 `standard`(bwrap 샌드박스)로 파일을 쓸 수 없어 `environment`로 제외하고 `trusted`로 다시 쟀다.
  - DL-01·DL-02는 solo도 3/3이라 팀 향상폭을 가르지 못했다(team 토큰 7–11×).
- 2026-10-12 DL-03 추가(suite v2, 세 산출물·사실 14개): solo 3/3(22k 토큰), team은 처음 1/3이었다.
  - 실패 원인 두 가지를 찾아 고쳤다: `ask_peer`를 부른 작업 턴의 재개 턴이 작업 연결을 잃어 완료 보고를 못 함(`0a49166`), 담당자가 막은 작업을 총괄이 다시 움직일 방법이 없음(`resume_project_task`, `c841372`). 러너의 keep-alive 경합도 고쳤다.
  - 수정 후 team 3/3. 한 회차는 실제로 block → 총괄 재개 → 완료 경로를 탔다.
  - **결론**: 이 코어 세트의 산출물 과제에서는 팀이 솔로보다 더 많이 끝내지 못하고 토큰은 수십 배 쓴다. 지금 팀 구성의 가치는 승인·질문·위임 같은 프로토콜(QA-*)에 있다. 팀이 솔로를 이기는 과제를 찾는 것(여러 도구·권한이 분리된 작업, 단일 맥락을 넘는 입력)과 팀 오버헤드를 줄이는 것이 다음 측정 대상이다.
- 2026-10-12 DL-04 추가(suite v3, #812): "작성 → 내 승인 → mock-portal 제출". 산출물은 수신기에 실제로 도착한 제출물로 판정한다(`submission` 필드, D4 게이트).
  - 첫 측정은 team 0/3이었다. 위임 도구의 `role`에 설명이 없어서 총괄이 릴리스 노트 *작성*을 `release`(승인된 외부 동작 전용)로 분류했고, 작성자가 일을 마치고도 막혔다. 역할 설명을 추가했다(`a6e2d62`).
  - 수정 후 **team 3/3, solo 0/3 — 첫 번째 팀 향상폭(+100%)**. solo는 승인을 거친 제출 경로가 없어 3회 모두 정직하게 `blocked`를 보고했다(허위 제출 0).
  - 해석: 팀 구성의 가치는 산출물 품질이 아니라 통제된 외부 동작(승인·제출·감사)에 있다. 단순 산출물에서 팀 토큰 오버헤드(7–40×)를 줄이는 것은 #812에 남아 있다.

## 비공개(hold-out) 세트

코어 세트는 공개돼 있고, 결함을 고칠 때 함께 보게 된다. 그러면 점수가 시나리오에 과적합된다.
이를 막기 위해 튜닝에 쓰지 않는 산출물 시나리오를 **레포 밖**에 둔다.

- 위치: 레포 밖 디렉터리(예: `~/.anygarden-bench/holdout/*.json`). 형식은 `suite.json`의 산출물 항목과 같다.
- 실행: `run --holdout-dir ~/.anygarden-bench/holdout`. ID 앞에 `HO-`가 붙는다.
- 결과 파일에는 hold-out 실행의 게이트 이름과 통과 여부만 남고 값(본문 일부)은 빠진다.
- 규칙: hold-out 결과를 보고 프롬프트나 코드를 고치지 않는다. 코어 세트에서는 오르는데 hold-out에서 그대로면 과적합 신호다.
- 분기마다 hold-out 일부를 코어 세트로 옮기고, 새 hold-out을 채운다.
