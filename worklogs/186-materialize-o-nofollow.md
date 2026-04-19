# harden(machine): use O_NOFOLLOW for agent-dir writes (#186)

- Commit: `747affa`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #186

## Situation

`_materialize_agent_dir`(`packages/machine/doorae_machine/spawner.py`)와 `ManifestStore`가 에이전트 디렉토리 안에 파일을 쓸 때 `Path.write_text` / `Path.write_bytes`를 사용했다. 이 메서드는 최종 path component가 symlink일 경우 기본적으로 follow하여 symlink가 가리키는 **다른** 파일에 쓴다. 이전 세션에서 악의적 에이전트가 `workspace/MEMORY.md`(또는 앞으로 materialize가 생성할 다른 슬롯)에 심어둔 symlink가 있으면 다음 spawn의 materialize 타이밍에 해당 symlink를 따라가 데몬 프로세스 권한으로 임의 경로에 쓰기가 발생할 수 있었다.

리뷰(2026-04-19) P2-6 항목. `workspace/`가 prune에서 제외되는 규칙 때문에 이전 세션 에이전트가 남긴 symlink가 살아남는다는 점이 핵심 전제.

## Task

- symlink follow를 거절하는 write 헬퍼를 모듈 단위로 도입하고, 에이전트 디렉토리에 닿는 모든 write 지점을 그 헬퍼로 교체
- `workspace/MEMORY.md` seed처럼 기존에 `if not exists()`만 확인하던 분기가 dangling symlink를 "없는 파일"로 착각하지 않도록 보강
- 기존 materialize/manifest_store 테스트 회귀 방지
- symlink 방어 동작을 단위 + 통합 레벨 모두에서 테스트로 고정

## Action

- 신규 모듈 `packages/machine/doorae_machine/safefs.py` — `safe_write_text`, `safe_write_bytes` 헬퍼. `os.open(path, O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW, mode)` + 명시적 `os.chmod`로 umask 무시
- `packages/machine/doorae_machine/spawner.py` — materialize의 write 6곳을 전부 헬퍼로 교체: AGENTS.md, files 맵 루프, `.claude/settings.json` 기본값, 엔진별 `.env`, `workspace/MEMORY.md` seed, Gemini `workspace/AGENTS.md`·`workspace/CLAUDE.md` real-copy. MEMORY.md 분기에는 `not memory_md.is_symlink()` 가드 추가
- `packages/machine/doorae_machine/manifest_store.py` — `save`, `update_desired_state`의 `write_text + chmod` 쌍을 `safe_write_text(mode=0o600)` 한 호출로 압축
- `packages/machine/tests/test_safefs.py` — 9개 단위 테스트: 생성·덮어쓰기, symlink 거절(regular target, dangling), 커스텀 mode, UTF-8, bytes, 부모 디렉토리 symlink limitation (최종 component만 가드)
- `packages/machine/tests/test_materialize.py` — `TestMaterializeRefusesSymlinkFollow` 클래스 2케이스: MEMORY.md가 outside 파일로 symlink된 상태에서 materialize가 victim 파일을 건드리지 않음, Gemini bridge 재생성이 symlink를 정리하고 real file로 남김

## Decisions

`.tmp/plan-186-materialize-o-nofollow.md`의 대안 비교:

- **A — `O_NOFOLLOW` 기반 low-level write 헬퍼** ← 선택. POSIX 표준이고 Linux/macOS 모두 지원, 코드 변경 국소
- **B — 사전 `lstat` 체크만 강화**: lstat과 open 사이 race 창 여전
- **C — `renameat2` atomic replace (Linux-only)**: 플랫폼 제한이 큼. 필요하면 bridge 파일에 후속 추가 가능
- **D — chroot/syscall 인가 레이어**: 과함

결정적 근거: 기존 unlink 체크와 중첩 적용으로 "check + write refusal"이 defense-in-depth를 이루고, 코드 변경은 8곳의 1-line 교체로 끝난다. 공통 헬퍼를 별도 모듈(`safefs.py`)로 분리해 테스트 표면도 한 곳에 모음.

가정:
- 최종 component만 가드. 부모 디렉토리가 symlink면 여전히 traverse — agent_root 부모는 데몬 소유라 에이전트 sandbox로 tamper 불가. 이 전제가 깨지면(예: 에이전트가 agent_root를 직접 쓸 수 있는 환경) 본 방어는 불충분해지므로 `openat` 기반 경로별 resolve로 격상 필요
- `.env` write는 이슈 #184에서 제거될 예정이지만 중간 상태 노출을 줄이기 위해 이번 patch에서도 함께 가드

## Result

- `uv run pytest` machine 244개(기존 233 + 신규 11) 전체 통과
- `harden/186-materialize-o-nofollow` 브랜치, 5개 파일 변경 (+278/-23)
- 리뷰 P2-6 항목 해결. 이슈 #181 (Gemini 격리 강화)이 후속으로 머지돼도 이번 변경의 위 레이어에서 symlink 경로가 계속 방어됨
- 관측 가능한 런타임 동작은 변화 없음 — symlink가 없는 정상 경로에서는 결과 동등 (mode는 동일, 내용 동일)
