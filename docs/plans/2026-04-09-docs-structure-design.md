# Doorae 문서 구조 설계

## 개요

프로젝트 문서를 `docs/`로 통합하고, 구현 상태를 자동 추적하는 구조를 정의한다.
사람과 에이전트 모두가 소비자이며, 스킬이 코드와 대조하여 STATUS.md를 자동 갱신한다.

## 디렉토리 구조

```
docs/
├── _index.md                     # docs/ 전체 안내
├── STATUS.md                     # 기능별 구현 상태 (자동 갱신)
├── ARCHITECTURE.md               # 현재 아키텍처
├── API.md                        # REST + WebSocket 레퍼런스
├── DEVELOPMENT.md                # 개발 가이드
├── design/
│   ├── _index.md                 # 설계 문서 목록 + 읽는 순서
│   └── 01~10-*.md                # episodes/impl/ → 여기로 이동
├── plans/
│   ├── _index.md                 # 계획 문서 목록
│   └── week1~5, web-ui 등       # episodes/impl/plan/ → 여기로 이동
└── decisions/
    ├── _index.md                 # ADR 목록
    └── NNN-*.md                  # 설계 결정 기록
```

## 파일 포맷

### _index.md
frontmatter(title, description, updated) + Contents 테이블

### STATUS.md
frontmatter(auto_generated: true) + Summary 카운트 테이블 + 패키지별 Feature/Status/Files/Notes 테이블
Status: done, stub, reverted, planned, partial

### API.md
엔드포인트별 Method/Path/Request/Response + WebSocket 프레임 표

### decisions/ (ADR)
frontmatter(id, title, status, date) + Context/Decision/Consequences

## 마이그레이션

- `episodes/impl/*.md` → `docs/design/`
- `episodes/impl/plan/*.md` → `docs/plans/`
- `episodes/impl/review/` → `docs/design/review/` 또는 삭제
- `docs/plans/2026-04-09-web-ui-design.md` → 유지
- `episodes/` → 비우면 삭제

## 스킬

`doorae-status` 스킬: 코드베이스를 스캔하여 STATUS.md를 자동 갱신
`doorae-api-doc` 스킬: FastAPI 라우터를 파싱하여 API.md를 자동 갱신
