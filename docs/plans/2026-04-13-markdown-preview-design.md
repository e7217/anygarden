# Markdown Preview for Chat Messages

**Date**: 2026-04-13
**Status**: Approved

## Goal

모든 채팅 메시지(에이전트 + 사용자)에 마크다운 렌더링을 적용하여 코드 블록, 리스트, 테이블 등 구조화된 콘텐츠를 시각적으로 표현한다.

## Approach

`react-markdown` + `remark-gfm` (GFM 전체 스펙). React 엘리먼트 트리 생성 방식으로 XSS 안전.

## Changes

| File | Change |
|---|---|
| `frontend/package.json` | `react-markdown`, `remark-gfm` 추가 |
| `frontend/src/components/MarkdownContent.tsx` | 신규 — 마크다운 렌더링 컴포넌트 |
| `frontend/src/components/MessageBubble.tsx` | `<p>{content}</p>` → `<MarkdownContent>` 교체 |
| `frontend/src/index.css` | 마크다운 요소 스타일 |

## MarkdownContent Component

커스텀 컴포넌트 매핑으로 DESIGN.md 스타일 적용:

- **inline code**: `bg-[var(--color-surface-alt)]` warm white + monospace
- **code block**: `bg-[var(--color-surface-dark)]` warm dark + 밝은 텍스트
- **link**: `color: var(--color-brand)` (Notion Blue) + hover underline
- **blockquote**: 왼쪽 `2px solid var(--color-border)` + muted 텍스트
- **table**: whisper border + `var(--color-surface-alt)` 헤더 배경
- **heading**: 버블 내 비례 축소 (h1=16px, h2=15px, h3=14px bold)
- **p, ul, ol**: 기존 DESIGN.md 타이포그래피 규칙

## Style Principles

- 기존 `text-sm` (14px) 유지
- 코드블록: `border-radius: var(--radius-md)`, overflow-x auto
- 마크다운 없는 plain text는 기존과 동일 렌더링
- raw HTML 기본 비활성 (react-markdown 기본 동작)
