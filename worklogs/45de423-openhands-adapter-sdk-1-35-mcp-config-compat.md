# fix(agent): openhands 어댑터를 SDK 1.35+ mcp_config 스키마에 맞춤 (#525)

- Commit: `45de423`
- Author: Changyong Um
- Date: 2026-07-13T17:14:21+09:00
- PR: —

## Situation

운영 환경의 openhands 엔진 에이전트가 대화에 응답하지 못했다. 원인은 LLM/vLLM이 아니라 **openhands-sdk 버전 드리프트**였다. `anygarden-agent` 0.10.0의 openhands 어댑터는 머티리얼라이즈된 `.mcp.json`을 FastMCP `{"mcpServers": {...}}` envelope 그대로 `Agent(mcp_config=...)`에 전달한다. openhands-sdk가 `1.21` → `1.35`로 올라가면서 `Agent.mcp_config` 타입이 `dict[str, Any]` → **`dict[str, MCPServer]`**(서버 맵)로 바뀌었고, 그 결과 envelope의 `mcpServers`가 "서버 이름"으로 오인되어 중첩된 `anygarden` 엔트리가 `ValidationError: Extra inputs are not permitted`로 거부됐다. 어댑터의 생성 fallback은 `except TypeError`만 처리해 pydantic `ValidationError`를 놓쳤고, 그래서 **LLM 호출이 시작되기도 전(메시지 수신 ~4ms 후)에 하드 실패**했다. `openhands-sdk`/`openhands-tools` 핀이 상한 없는 `>=1.21`이라 breaking 릴리스 1.35가 조용히 설치된 것이 근본 트리거였다.

## Task

- openhands 어댑터를 openhands-sdk **1.35+**의 `mcp_config` 스키마에 맞춘다.
- shape 불일치가 다시 발생해도 에이전트 전체가 죽지 않고 "MCP 없이"라도 부팅되도록 방어한다.
- 상한 없는 버전 핀을 닫아 다음 breaking 릴리스가 다시 조용히 들어오지 못하게 한다.
- fake-SDK 기반 기존 테스트로는 못 잡던 **실제 1.35 SDK** 경로를 검증에 포함한다.

## Action

`packages/agent/anygarden_agent/integrations/openhands_engine.py`:
- `_manifest_to_mcp_config(raw)` 헬퍼 신설 — `_load_mcp_manifest`가 돌려준 envelope에서 `mcpServers` 맵을 unwrap한 뒤 SDK 자체의 `openhands.sdk.mcp.config.coerce_mcp_config`로 정규화한다. 이는 SDK 내부 로더(`plugin/loader.py`, `skills/skill.py`)가 쓰는 `coerce_mcp_config(config["mcpServers"])` 규약과 동일하다. FastMCP 정규화라 `.mcp.json`의 `type/url/headers` 항목이 그대로 흡수된다. `coerce_mcp_config`가 없을 때(pre-1.35 SDK 또는 `openhands.sdk`를 스텁하는 테스트 fake)는 unwrap한 서버 맵으로 폴백한다.
- 구성부에서 `_load_mcp_manifest(...)`를 `_manifest_to_mcp_config(...)`로 감싸 `Agent(mcp_config=...)`에 넘긴다. `_load_mcp_manifest` 자체는 envelope 반환을 유지(기존 계약).
- Agent 생성 fallback을 `except TypeError` → `except (TypeError, ValidationError)`로 확장. shape 불일치 시 `mcp_config`를 떼고 재구성하며 `openhands.mcp_config_rejected_by_sdk`를 남긴다.

버전 핀:
- `packages/agent/pyproject.toml`, `packages/machine/pyproject.toml`의 `openhands-sdk`/`openhands-tools`를 모두 `>=1.35,<2`로.

테스트(`tests/test_integrations/test_openhands_engine.py`):
- 기존 `test_mcp_config_passed_to_agent_when_manifest_exists`의 단언을 unwrap된 서버 맵(`manifest["mcpServers"]`)으로 갱신.
- `test_agent_falls_back_when_sdk_raises_validation_error` 추가 — fake Agent가 실제 pydantic `ValidationError`를 던지게 하여 widened fallback으로 graceful degrade됨을 검증.
- `test_manifest_coerced_to_server_map_with_real_sdk` 추가 — 실제 1.35 SDK로 envelope가 `dict[str, MCPServer]`로 coerce되는지 검증(SDK 미설치 시 `importorskip`으로 skip). worktree venv에 1.35를 실제 설치해 실행 확인.

## Result

- worktree venv에 openhands-sdk/tools **1.35.0**이 의존성 충돌 없이 설치됨(버전 점프 자체가 깨끗함).
- 테스트: agent **481 passed**, openhands 엔진 **64 passed**, machine **387 passed(2 skipped)**; ruff clean. 실 1.35에서 envelope → `dict[str, MCPServer]` coerce 확인, `ValidationError` fallback 확인.
- 1.21→1.35 점프가 테스트된 경로에서 다른 breaking change를 유발하지 않음을 확인.
- 잔여: 완전한 라이브 룸 턴(에이전트가 MCP 툴을 실제로 붙여 대화하는 경로) 검증은 running cluster+machine+agent+LLM이 필요해 이 개발 박스(1.21.1)에서는 불가 — 배포/CI(1.35) 환경에서 확인 필요.
