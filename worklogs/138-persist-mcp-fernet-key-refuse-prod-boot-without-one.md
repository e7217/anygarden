# fix(cluster): persist MCP Fernet key + refuse prod boot without one (#138)

- Commit: `e1fa717`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #138 (issue)

## Situation

`DOORAE_MCP_SECRETS_KEY`가 설정되지 않으면 cluster는 매 부팅 시 새 Fernet 키를 생성하는 ephemeral fallback을 탔다. 이전 프로세스에서 admin UI로 attach한 MCP credential(GitHub PAT, Linear API key 등)은 해당 프로세스의 휘발성 키로만 암호화되어 DB에 저장되어 있었기에, 서버 재시작 직후 첫 MCP 툴 호출에서 `Failed to decrypt MCP credentials — the Fernet key may have been rotated` 에러가 발생하고 뒤이어 `machine_bus.unregister`가 트리거되어 머신 연결까지 끊어졌다. 사용자는 이 증상을 "Claude가 GitHub에 권한 없다고 한다"로 체감했다.

조사에서 두 문제가 드러났다:

1. `app.py:285-287`이 `MCPSecrets.from_config_key()`를 **항상 `dev_mode=True`로 호출** — production이든 dev든 동일하게 ephemeral fallback을 수용. `from_config_key` docstring("Production refuses without a key")과 정면 모순.
2. 영속화 경로 부재 — `~/.doorae/jwt_secret` 패턴이 JWT엔 있지만 MCP 키엔 없었다. 운영자가 env를 명시하지 않으면 매 재시작마다 MCP가 깨짐.

## Task

- MCP credential이 재시작 후에도 복호화 가능하도록 키를 영속화
- `DOORAE_DEV=0` (production) + 키 미설정일 때 부팅을 거부해 오운영 조기 감지
- 운영자가 별도 env 설정 없이도 로컬 개발에서 "그냥 동작"하게 (JWT 패턴과 일관)
- 기존 568개 cluster 테스트 회귀 없음
- 환경 변수 surface 문서화 (신규 개발자 온보딩)

## Action

- `packages/cluster/doorae/app.py:275-327` — MCP 키 해석 블록 재작성
  - 우선순위 1: `config.mcp_secrets_key` (env)
  - 우선순위 2: `~/.doorae/mcp_secrets_key` 파일. 존재하면 read, 없으면 `Fernet.generate_key()`로 생성 + `chmod 0o600`
  - 파일 I/O는 `try/except OSError`로 감싸 read-only HOME 등의 경우 빈 문자열로 폴백 → `from_config_key`가 `dev_mode`에 따라 분기
  - `dev_mode=config.dev` 전달 (기존 `dev_mode=True` 하드코딩 제거)
- `.env.example` (repo root, 신규) — 모든 `DOORAE_*` 변수 설명, `DOORAE_MCP_SECRETS_KEY` 생성 커맨드 포함, "키 분실 시 모든 MCP credential 무효화" 경고 명시
- `packages/cluster/README.md` — "Environment" 섹션 추가, 위 변수 3개(JWT/MCP/DEV) 요약 + `.env.example` 참조
- `packages/cluster/tests/test_mcp_secrets_persistence.py` (신규) — 4 케이스:
  - `test_explicit_env_key_wins_over_file` — 우선순위 1 > 2 검증
  - `test_file_fallback_persists_across_restarts` — 2회 부팅으로 파일 기반 키 동일성 + 0o600 권한 검증
  - `test_dev_mode_allows_ephemeral_when_file_write_fails` — `mcp_secrets_key`를 디렉토리로 pre-create해 write_text 실패 유도, `dev=True`에서 ephemeral로 degrade
  - `test_production_refuses_boot_without_key_or_file` — 같은 조건 + `dev=False`에서 `MCPSecretsUnavailable` raise

테스트 인프라 메모: `conftest.py`의 `config` fixture가 항상 `mcp_secrets_key`를 pre-fill해서 이번 테스트만 별도 `_fresh_config()` 헬퍼로 우회. ASGITransport가 lifespan을 트리거하지 않는 문제는 `app.router.lifespan_context(app)`를 직접 async-with로 구동해 해결 (다른 테스트에서 확인된 패턴).

## Decisions

`.tmp/plan-138-persist-mcp-secrets-key.md` 기반:

**영속화 전략**
- A. env-only, 파일 fallback 없음 — 12-factor 정론이지만 신규 사용자마다 수동 생성 필요
- B. JWT 패턴 모방 (env 우선, 파일 fallback) → **선택**
- C. 파일 only, env 없음 — 컨테이너 배포에서 secret 주입이 표준이므로 부적합

결정적 근거: 이미 `jwt_secret`이 같은 패턴(`~/.doorae/jwt_secret`)이라 일관성 최우선. env가 우선순위 1이라 K8s/Docker에서는 secret으로 주입하는 경로 보존. 파일은 로컬 개발 기본값으로 편의 제공.

**`dev_mode` 전달**
- A. `dev_mode=config.dev` → **선택**
- B. `dev_mode=True` 유지 — 현재 버그 상태, 재발 보장

결정적 근거: 사용자가 겪은 증상 자체가 `dev_mode=True` 하드코딩에서 비롯. 이 수정이 본 이슈의 핵심. 파일 fallback이 먼저 작동하므로 일반 사용자 체감은 "아무것도 안 해도 돌아감" 유지되면서 production gate만 복원.

**마이그레이션 경로**
- 기존 사용자가 ephemeral 키로 암호화해둔 MCP instance는 이번 변경 직후에도 복호화 불가 (이전 프로세스 키가 사라짐). 이는 근본 문제라 자동 복구 불가.
- 해결: admin UI에서 detach → 재attach로 새 영속 키로 재암호화. 이슈 #138 설명과 README에 이 정보 포함.

**파일 위치 `~/.doorae/mcp_secrets_key`**
- JWT와 같은 디렉토리라 권한/백업/삭제 정책이 일관. 별도 경로 선택하면 운영자가 두 곳을 관리해야 함.

**OSError 처리**
- Write 실패를 silent ignore하고 `from_config_key`가 처리 — dev에서는 ephemeral로 degrade, prod에서는 명시 env 없으면 hard fail. 두 모드 모두에서 올바른 동작.

가정: HOME 디렉토리가 cross-restart 동일. K8s에서 이를 어기면 volumes/persistentVolumeClaim을 붙이거나 env를 설정해야 함 — 이 경우 `DOORAE_MCP_SECRETS_KEY`가 표준 경로.

## Result

- 로컬 개발(env 미설정) 재시작 반복 시 기존 MCP attachment 유지
- `DOORAE_MCP_SECRETS_KEY` env 설정 시 파일 무시하고 env 값 사용 (K8s secret 등 명시 주입 경로)
- `DOORAE_DEV=0` + 키 미설정 + 파일 쓰기 불가 시 boot에서 `MCPSecretsUnavailable` raise
- `DOORAE_DEV=1` + 동일 조건 시 ephemeral 키로 degrade + 경고 로그
- cluster 테스트 572/572 통과 (이전 568 + 신규 4)
- 후속 과제 (이슈 설명에 명시): 복호화 실패가 `machine_bus.unregister`를 트리거하는 graceful degradation 개선은 별건으로 분리
- 사용자 마이그레이션 필요: 기존 MCP instance를 admin UI에서 detach 후 재attach (새 영속 키로 재암호화)
