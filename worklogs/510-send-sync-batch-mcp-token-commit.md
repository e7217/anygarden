# fix(scheduler): send_sync_batch가 민팅한 self-MCP 토큰을 커밋 — 재spawn 에이전트 /mcp/rpc 401 수정 (#510)

- Commit: `c9c512c` (c9c512cf756bc406242e4efcd956566ca113cb63)
- Author: Changyong Um
- Date: 2026-07-02T20:37:27+09:00
- PR: #510 (issue)

## Situation

로컬 인스턴스에서 에이전트에게 task를 부여하면 에이전트는 응답하지만 task 상태가 `in_progress`/`done`으로 전이되지 않는 문제가 관찰됐다. 원인은 codex-cli 에이전트가 anygarden self-MCP 서버(`POST /mcp/rpc`)에 `ANYGARDEN_AGENT_TOKEN`(= `anygarden_mcp_token`)으로 붙을 때 401 "Invalid agent token"을 받아 `mark_task_status` MCP 툴이 로드되지 않았기 때문이다. 그 토큰이 무효인 근본 원인은 `AgentLifecycle.send_sync_batch`가 토큰을 민팅만 하고 **커밋하지 않은 채** 프레임에 실어 보냈기 때문이다.

## Task

- `send_sync_batch`가 `_build_sync_frame → _acquire_anygarden_token`으로 `db.add`한 `AgentToken` 행을 실제로 영속시킨다(401 해결).
- 동일 스케줄러 생애 내 배치 재호출이 같은 토큰을 재사용하도록(멱등) 한다.
- 정상 경로(`request_start`, `handle_token_request`)의 커밋 패턴과 정합화한다.
- 회귀 없음(특히 `send_sync_batch`/토큰/MCP 경로).

## Action

- `packages/cluster/anygarden/scheduler/lifecycle.py::send_sync_batch` (약 L536 이후): 프레임 빌드 for 루프 뒤, `async with self._db_factory() as db:` 블록 안에 `await db.commit()` 한 줄 추가. 이로써 스테이징된 토큰 행이 영속되고 `_acquire_anygarden_token`의 `after_commit` 훅(`_promote_pending_on_commit`)이 발화해 durable `_token_cache`가 채워진다.
- `packages/cluster/tests/test_send_sync_batch_token_commit.py` (신규): `cluster_external_url`을 설정한 `AgentLifecycle` + codex-cli 에이전트로 `send_sync_batch`를 태워 (1) 프레임의 `anygarden_mcp_token`이 `agent_tokens`에 영속되는지, (2) 배치 2회 호출 시 같은 토큰 재사용 + 중복 행 없음(멱등)을 검증. 기존 `test_declarative_reconcile.py::test_send_sync_batch`는 `cluster_external_url` 미설정으로 토큰이 아예 안 민팅돼 이 버그를 못 잡았다.

## Decisions

- **채택 — `send_sync_batch`에 `await db.commit()` 추가.** `after_commit` 훅 주석이 *"a single listener covers multiple agents batched into one transaction (`send_sync_batch` / `handle_token_request` shapes)"*라고 이 경로의 배치 커밋을 명시적으로 전제하고 있어, 커밋은 원래 설계의 일부였고 호출만 누락된 것으로 판단.
- **기각 — 토큰 발급을 `token_request`(handle_token_request)로 이관:** 멀쩡히 발급·전송하던 spawn 프레임의 토큰 전달을 없애고 데몬-서버 왕복을 강제해 spawn 지연·회귀 위험이 커짐. 최소 수정 원칙에 반함.
- **기각 — 토큰 평문(암호화) 저장으로 cross-restart 멱등:** "토큰은 해시로만 보관" 원칙을 바꿔야 하고 이번 이슈 범위 이득이 없음.
- **가정/미해결:** `send_sync_batch`의 트랜잭션에는 토큰 민팅 외 커밋되면 안 되는 쓰기가 없다(조회 위주)는 전제 하에 커밋이 안전 — 향후 이 블록에 다른 쓰기가 추가되면 재검토. cross-restart 시 캐시(in-memory) 소실로 새 토큰 행이 누적되는 건 별도 견고성 이슈(superseded `agent_tokens` 정리)로 분리.

## Result

- codex 등 배치로 (재)spawn되는 에이전트가 유효한 self-MCP 토큰을 받아 `/mcp/rpc` 인증 통과 → `mark_task_status` 사용 가능 → task 상태 전이 정상화.
- `send_sync_batch` 재호출이 멱등(같은 토큰 재사용, 중복 행 없음).
- 신규 테스트 2개 통과, cluster 패키지 전체 1224 passed(무관한 deprecation 경고 1건). ruff clean.
- 후속(선택): cross-restart 토큰 행 정리 정책은 별도 이슈로 검토.
