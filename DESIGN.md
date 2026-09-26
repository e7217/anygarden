# Anygarden design system

This document describes the current product UI. The working design decision and responsive acceptance criteria are in [the approved redesign plan](docs/plans/2026-09-26-product-ui-ux-redesign-design.md).

## 1. Product character

Anygarden is a workspace for people and AI agents. Its interface should keep the conversation and current task readable, make system state easy to scan, and expose management controls when they are relevant. Supabase's clear navigation hierarchy and Resend's restrained data surfaces informed the layout; Anygarden uses its own identity and components.

The default appearance is light. Teal marks interactive intent. Green, amber and red mark success, caution and errors. Do not use teal as a health indicator or rely on colour alone to communicate state.

## 2. Colour and theme tokens

`packages/cluster/frontend/src/index.css` is the source of truth. Components consume semantic CSS variables through Tailwind classes and shadcn style primitives. Do not add fixed white or black backgrounds to product surfaces.

| Role | Token | Light | Dark |
| --- | --- | --- | --- |
| Page | `--color-background` | `#ffffff` | `#0b1412` |
| Primary text | `--color-foreground` | `#122522` | `#edf6f2` |
| Work surface | `--color-surface` | `#ffffff` | `#101b18` |
| Secondary surface | `--color-surface-alt` | `#f5f8f7` | `#15221e` |
| Raised surface | `--color-surface-elevated` | `#ffffff` | `#1b2b26` |
| Hover | `--color-surface-hover` | `#eef4f1` | `#20332d` |
| Selected | `--color-surface-selected` | `#e6f4f1` | `#183b35` |
| Primary action | `--color-brand` | `#0f766e` | `#0f766e` |
| Link or teal text | `--color-brand-text` | `#0b5f58` | `#5eead4` |
| Divider | `--color-border` | `#dce7e3` | `#30433a` |

The same file defines foreground variants, strong borders, focus, message surfaces, semantic statuses and paired avatar tones. Keep text on teal buttons white through `--color-on-brand`; use `--color-brand-text` for teal labels and links. The theme provider stores `anygarden-theme` and a small `index.html` script applies it before React paints.

## 3. Typography and spacing

Use the `Inter` system stack. The named scale is display 48px, title 32px, heading 24px, lead 20px, body 16px, caption 14px and badge 12px. Reserve 12px for compact metadata; forms, descriptions and empty states should normally use at least 14px. Headings use tight tracking and regular body text uses a 1.5 line height.

The spacing scale follows 4px steps. `--space-6` (24px) is the standard page section and card padding on wide screens, reduced on narrow screens. Dividers and surface changes carry hierarchy; elevation is for overlays and raised cards. Radius tokens range from 4px controls to 12px dialogs and full pill badges.

## 4. Components and feedback

Use the shared `components/ui` buttons, inputs, cards, dialogs, tables and tabs. Inputs use semantic surfaces and a visible focus ring. Primary actions use teal, secondary actions are neutral, and destructive actions use the danger token. Disabled, loading and error states stay visible and explain what happened.

Use `FeedbackProvider` for destructive confirmation and transient notices. Confirmation identifies the affected object and consequence; a failed action shows an actionable error. Dialogs fit within the viewport, scroll internally when needed, and keep actions reachable by touch or keyboard.

## 5. Layout and navigation

`PageShell` provides the shared sidebar and mobile top bar for full page areas. Chat uses its own three part workspace: project and room navigation, conversation, and an optional context rail for responsibilities, tasks and files. Desktop keeps the main work area central. At narrower widths, secondary rails become panels or drawers so they never squeeze the main content to a single character column.

Page headers show location, title, a short description when useful, then the main action and secondary actions. Empty states offer the next usable action: create a project, create a room, choose a room, add an agent, or upload a file. Forms reveal exceptional settings only when needed. Existing server fields and saved values must remain reachable.

## 6. Responsive behaviour

Check at 375px, 768px, 1024px and 1440px. No action may disappear beyond the horizontal viewport, overlap another action, or require hover on touch devices. A control should have a target of at least 44×44px where space allows. Tables and dense metadata may scroll within their own region; full page horizontal overflow is a defect. Chat input and dialog actions must remain usable with the mobile keyboard.

## 7. Accessibility and language

Text contrast targets WCAG AA (4.5:1 for normal text); focus indicators remain visible in both themes. Icons with no visible label need an accessible name. State is expressed with text or icons as well as colour. Reduced motion preferences apply to decorative animation.

Fixed interface copy lives in `src/i18n/catalogs/`. The locale provider persists `anygarden_locale`, defaults to Korean for Korean browsers and English otherwise, and changes visible copy without a reload. Use locale aware formatting for dates and numbers. Project names, user messages, agent output and arbitrary server details remain as supplied.

## 8. Verification

After a UI change, run the frontend build and relevant component and browser tests. Inspect the affected route at the four reference widths in both themes and languages. Check the actual pointer target on crowded headers and menus, keyboard focus order, dialog scrolling, empty states and the full login to room flow.
