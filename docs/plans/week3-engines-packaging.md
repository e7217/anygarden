# Week 3: 나머지 엔진 + CLI 패키징 + v0.1.0

> **목표**: Codex/OpenHands/Deep Agents 엔진 통합, PyPI 패키징, v0.1.0 릴리즈
> **산출물**: 4종 엔진 동작, `uvx doorae-{server,agent,client}` 공개 가능
> **정본 참조**: [08-operations.md](../08-operations.md) §8.3.4-§8.7, [06-mcp-integration.md](../06-mcp-integration.md)

---

## 1. 요약

Week 2까지 Claude Code + OpenAI 2종 엔진이 통합되었다. Week 3에서:
- **Codex, OpenHands, Deep Agents, Anthropic** 4종 엔진 어댑터 추가
- CLI 최종 정리 + 에이전트 프로필 YAML 예시 5종
- PyPI 릴리즈 워크플로 (GitHub Actions)
- v0.1.0 태깅 (Machine 스케줄링 미포함 — 채팅 전용)

---

## 2. 추가/수정할 파일

### doorae-sdk 엔진 어댑터 (4종 추가)

```
doorae-sdk/doorae_sdk/integrations/
├── codex.py                     # [50 LOC] integrate_with_codex
├── openhands.py                 # [50 LOC] integrate_with_openhands
├── deep_agents.py               # [50 LOC] integrate_with_deep_agents
└── anthropic.py                 # [50 LOC] integrate_with_anthropic (OpenAI와 유사)
```

### 에이전트 프로필 예시

```
doorae-sdk/examples/profiles/
├── pm.yaml                      # PM (claude-code)
├── tech-lead.yaml               # TechLead (codex)
├── coder.yaml                   # Coder (openhands)
├── analyst.yaml                 # Analyst (deep-agents)
└── host.yaml                    # Host/사회자 (openai)
```

### CI/CD

```
doorae-server/.github/workflows/
├── test.yml                     # pytest + ruff + mypy
└── release-pypi.yml             # 태그 시 PyPI 배포

doorae-sdk/.github/workflows/
├── test.yml                     # pytest + ruff + mypy + protocol-compat
└── release-pypi.yml             # 태그 시 PyPI 배포
```

---

## 3. 구현 단계

### Phase 3A: 엔진 4종 어댑터 (Day 1-2)

- [ ] `doorae_sdk/integrations/codex.py` — Codex SDK 통합
  - `integrate_with_codex(client, session)` 
  - 상태: `conceptual` (codex-sdk 0.50.x API)
- [ ] `doorae_sdk/integrations/openhands.py` — OpenHands 통합
  - `integrate_with_openhands(client, runtime)`
  - 상태: `conceptual` (openhands-ai 0.40.x EventStream API)
- [ ] `doorae_sdk/integrations/deep_agents.py` — Deep Agents 통합
  - `integrate_with_deep_agents(client, graph)`
  - 상태: `conceptual` (langgraph 0.2.x + deepagents 0.1.x)
- [ ] `doorae_sdk/integrations/anthropic.py` — Anthropic 직접 API
  - `integrate_with_anthropic(client, anthropic_client, model)`
  - 상태: `verified` (anthropic>=0.25)
- [ ] **검증**: 각 어댑터 mock 테스트 1개씩 = 4개

### Phase 3B: 프로필 예시 + CLI 최종 정리 (Day 3)

- [ ] `examples/profiles/*.yaml` 5종 작성 (§8.3.6 정본)
- [ ] `doorae_sdk/cli.py` 최종 정리:
  - `--engine` 6종 선택 (claude-code, codex, openhands, deep-agents, openai, anthropic)
  - `--profile` 파일 로드
  - `--model` 옵션 (엔진별)
  - `--room` 복수 지정 가능
  - 도움말 깔끔하게
- [ ] `doorae_sdk/cli.py`의 `client_main`:
  - `doorae-client --server ws://... --user me --room sprint-42`
  - `doorae-client admin init --email ...` → REST API 호출로 초기 유저 생성
  - `doorae-client admin token create --name PM --engine claude-code` → REST API
- [ ] **검증**: `doorae-agent --help`, `doorae-client --help` 출력 확인

### Phase 3C: CI/CD 워크플로 (Day 4)

- [ ] `doorae-server/.github/workflows/test.yml` (§8.7.1 정본)
  - Python 3.11 + 3.12 매트릭스
  - pytest + ruff + mypy
- [ ] `doorae-server/.github/workflows/release-pypi.yml` (§8.7.3 정본)
  - 태그 `v*` 시 `python -m build` + `pypa/gh-action-pypi-publish`
- [ ] `doorae-sdk/.github/workflows/test.yml` (§8.7.2 정본)
  - protocol-compat 잡 포함 (서버 저장소의 protocol.py와 해시 비교)
- [ ] `doorae-sdk/.github/workflows/release-pypi.yml`
- [ ] **검증**: GitHub에 push 후 CI 통과

### Phase 3D: v0.1.0 릴리즈 (Day 5)

- [ ] 서버/SDK 양쪽 `__version__ = "0.1.0"` 확인
- [ ] CHANGELOG.md 작성
- [ ] Git 태그 `v0.1.0` + push → PyPI 자동 배포
- [ ] `uvx doorae-server` + `uvx doorae-agent` + `uvx doorae-client` 동작 확인
- [ ] README 빠른 시작이 3분 이내에 따라할 수 있는지 확인

---

## 4. 테스트 전략

| 범주 | 위치 | 수 | 시나리오 |
|------|------|---|---------|
| 단위 | `test_integrations/test_codex.py` | 2 | mock 연결, mock 응답 |
| 단위 | `test_integrations/test_openhands.py` | 2 | mock |
| 단위 | `test_integrations/test_deep_agents.py` | 2 | mock |
| 단위 | `test_integrations/test_anthropic.py` | 2 | mock |
| 통합 | `test_cli.py` | 4 | agent --help, client --help, profile 로드, 엔진 선택 |
| **합계** | | **12** | 누적 72개 (W1 40 + W2 20 + W3 12) |

---

## 5. 완료 기준 (v0.1.0)

- [ ] 6종 엔진 어댑터 동작 (2 verified + 4 conceptual)
- [ ] `uvx doorae-server` → `uvx doorae-agent --engine openai` → `uvx doorae-client` 3자 대화 동작
- [ ] PyPI에 `doorae-server` + `doorae-sdk` 배포
- [ ] GitHub Actions CI 2개 저장소 모두 통과
- [ ] protocol-compat 테스트 통과
- [ ] 72개 테스트 통과
- [ ] v0.1.0 태그 + CHANGELOG

---

## 6. v0.1.0의 한계 (명시)

v0.1.0은 **채팅 전용** 릴리즈이다. Machine 스케줄링(§10)은 포함하지 않는다:
- Standalone 모드만 가능 (사용자가 직접 `uvx doorae-agent` 실행)
- 선언적 에이전트 생성(`POST /api/v1/agents`) 없음
- Machine 등록/Daemon 없음
- 스케줄러 없음

이것들은 Week 4-5에서 추가되어 v0.2.0에 포함된다.

---

## 7. 참고

- [08-operations.md](../08-operations.md) §8.3.4 엔진 어댑터, §8.7 CI/CD
- [06-mcp-integration.md](../06-mcp-integration.md) — MCP는 엔진이 자체 관리, SDK/서버 무관
