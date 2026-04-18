# feat(cluster): pass through full skill directory into agent spawn (#123)

- Commit: `1e74c0e` (1e74c0e0130c5a9cc214abe5aa768f7c5e95a149)
- Author: Changyong Um
- Date: 2026-04-19T01:20:11+09:00
- PR: #123

## Situation

Phase 1 MVP (#121 / #122) 는 `skills/<name>/SKILL.md` 한 파일만 에이전트
디스크에 materialize 했다. 실제 Vercel / Anthropic 이 배포하는 다수 스킬은
`scripts/`, `references/`, `data/`, `hooks/` 같은 보조 파일을 SKILL.md 와
함께 묶어 내린다. 이 보조 파일이 빠지면 SKILL.md 가 참조하는 스크립트가
런타임에 NotFoundError 로 죽으므로 스킬 본래 기능이 동작하지 않는다.
`skill_library.extra_files` / `content_hash` 컬럼은 Phase 1 migration 018 에
이미 준비됐지만 활용이 막혀있던 상태.

## Task

- 등록 시점에 GitHub tree API 가 알려준 `skills/<name>/` 하위 blob 을 전부
  페치해 `extra_files` JSON 컬럼에 `{rel_path: body}` 저장.
- `_build_sync_frame` 의 skill merge 에서 SKILL.md + extra_files 모두
  materialize 되도록 확장 (기존 "AgentFile 우선" 규칙 유지).
- `content_hash` 를 `sha256(skill_md)` → canonical-tree 해시 (정렬된 path
  + body hash concat) 로 업그레이드 — 보조 파일 변경까지 drift 감지.
- 악성 / 비정상 거대 스킬 거절: per-file 1MB, per-skill total 10MB.
- 허용되지 않은 확장자는 등록 거절 (화이트리스트 확장은 범위 외).
- 머신 패키지 / DB 스킴 / 프론트엔드는 건드리지 않음 (Phase 1 의 재료로 전부 커버).

## Action

- `packages/cluster/doorae/skills_library/github_fetcher.py`
  - `SkillFetchResult.extra_files: dict[str, str]` 필드 추가.
  - `fetch_skill` 이 tree 응답에서 `skills/<name>/` 하위 non-SKILL.md blob
    을 수집 → tree `size` 메타로 per-file / total 제한 사전 검증 →
    `asyncio.gather` 로 병렬 raw 페치 → 결과 dict 반환.
  - 허용 외 확장자 발견 시 `UnsupportedSkillFileError` (새 예외).
    서버 측 화이트리스트는 `agent_files._ALLOWED_EXTENSIONS` 를 single
    source 로 재사용.
- `packages/cluster/doorae/skills_library/service.py`
  - `register`: `SkillFetchResult.extra_files` → `SkillLibraryEntry.extra_files`.
  - `content_hash = _canonical_tree_hash({SKILL.md path: skill_md, **extra_files})`.
    헬퍼는 `sha256("{path}\n{sha256(body)}\n" 각 정렬 행 concat)`.
  - `resolve_for_agent` 가 SKILL.md 경로와 extra_files 항목 전부 반환.
- `packages/cluster/doorae/scheduler/lifecycle.py`
  - `_build_sync_frame` 의 skill merge 루프가 `extra_files` 도 `setdefault`
    로 추가 — AgentFile 과 경로 충돌 시 AgentFile 우선 (기존 규칙 유지).
- `packages/cluster/doorae/api/v1/skills.py`
  - `SkillOut.scripts_detected` 필드명 유지, 값 의미가 "감지됨" → "실제
    페치된 경로 목록" 으로 바뀜 (UI 에서 `.length` 만 쓰므로 호환).
- 테스트 확장 (+9):
  - `tests/test_skills_library_github.py` — extra_files 페치, 크기 위반,
    허용 외 확장자 거절 (3 건 추가).
  - `tests/test_skills_library_service.py` — extra_files 저장, canonical
    hash 결정성, body-only 변경 감지, path-only 변경 감지 (4 건 추가).
  - `tests/test_lifecycle_skills.py` — extra_files merge / AgentFile 우선
    (2 건 추가).

## Decisions

계획 `.tmp/plan-123-full-directory-passthrough.md` §3.2 의 결정을 그대로
따랐다.

- **A1 (등록 시 전량 페치 + DB 저장)** — spawn path 를 네트워크-독립으로
  유지. Phase 1 의 pinned-SHA 철학과 같은 결. 초기 등록이 수백 ms-초 단위
  더 느려지지만 spawn 속도/안정성 우선.
- **B1 (fetcher 단 크기 제한)** — tree 응답의 `size` 메타로 사전 거절 →
  악성 거대 blob 을 메모리에 올리지 않음. service / DB constraint 단에서
  체크하면 이미 bandwidth 낭비.
- **C1 (canonical-tree hash)** — body 포함 hash 라 Phase 1 bump fix 경로
  (`body_changed = old_hash != new_hash`) 가 extra_files 변경까지 자동
  감지. Phase 2 / 5 의 stale check 정확도에도 바로 기여.
- **D1 (허용 외 확장자 등록 거절)** — silent skip (D2) 은 partial-install
  혼란, 화이트리스트 선제 확장 (D3) 은 근거 없는 공격 표면 확대. 실제
  사용자 케이스가 나오면 후속 이슈로 화이트리스트 확장.

**가정** — 스킬 하나의 파일 수가 수십 개 이하라 asyncio.gather 병렬 페치
가 GitHub 익명 60/h rate limit 과 호환. 수백 파일 스킬이 등장하면 fetcher
에 concurrency 제한 추가 필요 (Phase 5 에서 `GITHUB_TOKEN` 지원과 함께
재검토).

**위반 시 재검토** — 실제 사용자 스킬에서 허용 외 확장자 빈발 →
화이트리스트 확장 필요. canonical hash separator 충돌은 body 에 개행 있어도
sha256 64자 hex 덕에 발생 불가.

## Result

- `uv run pytest` — 452 passed, baseline 443 대비 +9 (모두 신규 스킬 테스트).
  머신/에이전트 패키지 영향 없음.
- `uv run ruff check` — 변경 7 개 파일 clean.
- 동작: admin 이 기존 등록된 web-design-guidelines 재등록 → content_hash
  가 canonical-tree 해시로 업데이트 + body 동일이면 no-bump.
  anthropics/skills@pdf 같은 scripts 포함 스킬 신규 등록 → 에이전트 디스크
  에 scripts/*.py 도 함께 materialize.
- 시각/E2E 수동 스모크는 워크트리 환경 제약상 미수행 — main 병합 후 확인.
- Phase 2 (#125), Phase 5 (#126) 이 의존하는 `content_hash` 정의가
  canonical-tree 해시로 확정됐으므로 두 phase 구현 시 재검토 포인트는
  해소됨. Phase 2 audit log `detail.before_hash/after_hash` 는 이 hash 를
  그대로 저장.
