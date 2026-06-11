# feat(design-system): unify blue, radius alias, drop sidebar wordmark, agent-option parity (#435)

- Commit: `31edb8e` — Step 5 (polish, part 1) of #435 migration
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

감사의 저심각도/구조 항목들: 거의 동일한 블루 3개(`#0075de`/`#097fe8`/`#005bab`)가 드리프트, `--radius`와 `--radius-xs`가 둘 다 4px로 중복, 뱃지 기본 텍스트가 AA 미달(`#097fe8` on `#f2f9ff` ≈3.4:1), 사이드바 상단 'Anygarden' 워드마크(사용자가 직접 제거 요청), 그리고 동일 역할 에이전트의 옵션이 진입점(메뉴 vs 통합 설정 다이얼로그)에 따라 달라 기능이 누락되던 문제.

## Task

- 블루를 하나의 base에서 파생하도록 정리(focus/tint/ring)
- radius 중복 제거(별칭)
- 뱃지 텍스트 AA 통과(토큰만으로)
- 사이드바 워드마크 제거
- AgentSettingsDialog에 메뉴와 동일한 per-agent 액션(Delete, context-window 토글) 노출

## Action

- `src/index.css`: `--color-brand-focus` `#097fe8`→`#0075de`(=brand), `--color-brand-tint-text` `#097fe8`→`#005bab`(AA), `--color-ring`→`#0075de`, `--shadow-focus` 링 색 `rgba(9,127,232,…)`→`rgba(0,117,222,…)`. `--radius-xs: var(--radius)` 별칭.
- `src/components/Sidebar.tsx`: 헤더의 `MessageSquare` 아이콘 + 'Anygarden' `<h1>` 제거, 컨트롤을 `justify-end`로 우측 고정. (`MessageSquare` import는 다른 사용처가 있어 유지.)
- `src/components/AgentSettingsDialog.tsx`: `onDelete?`/`contextWindowOptOut?`/`onToggleContextWindowOptOut?` prop + sticky footer 추가(EyeOff 토글 + Trash2 Delete, destructive 토큰). show-when-permitted.
- `AdminMachines.tsx`·`Sidebar.tsx`: `handleDeleteAgent`를 `Promise<boolean>` 반환으로 바꿔 다이얼로그가 실제 삭제 시에만 닫히도록 하고, 기존 핸들러를 다이얼로그에 전달.
- `AgentSettingsDialog.test.tsx`: parity footer 테스트 3개 추가(미공급 시 숨김 / Delete·토글 발화 / aria-checked).

## Decisions

- **뱃지 AA를 토큰만으로 해결**: badge.tsx의 default variant가 `--color-brand-tint-text`를 읽으므로 토큰값(`#005bab`)만 바꾸면 컴포넌트 수정 없이 AA 통과. 제안 문서의 "tokens first, components fall out for free" 그대로.
- **워드마크: 아이콘까지 제거**: 사용자 요청 "사이드바 상단 anygarden 제거"를 브랜드 식별자(아이콘+워드마크) 전체 제거로 해석. h-14 행은 collapse/close 컨트롤 때문에 유지하되 좌측 식별자를 비워 시각적으로 정돈. 식별성은 룸 스위처/브레드크럼이 담당(제안 근거).
- **parity = 완전(Delete 포함)**: 사용자 승인. 파괴적 Delete를 설정 다이얼로그에 두되 기존 `confirm()` 흐름을 재사용하고 성공 시에만 닫음. `handleDeleteAgent`를 boolean 반환으로 바꾼 이유 — confirm 취소 시 다이얼로그가 닫히는 오작동을 막기 위함. 메뉴 호출부는 반환값을 무시(void 호환).

## Result

- `npm run build`(tsc) 통과. AgentSettingsDialog 9개(기존6+신규3)·Menu 14·Sidebar 8 테스트 통과.
- 블루 1 base로 수렴, radius 단일 소스, 뱃지 AA ≈5.9:1, 사이드바 워드마크 제거, 에이전트 옵션이 진입점과 무관하게 동일.
- 남은 폴리시(하드코딩 색상 잔여분·status 점)는 step 5 part 2에서.
