# /design-sync notes — Anygarden UI

Synced design system: `packages/cluster/frontend/src/components/ui/` (the shadcn/Radix primitives).
Project: **Anygarden Design System** (`ceb6b73e-802b-4526-b94c-69d3c68bf4fc`).

## How this repo is wired (it's an app, not a library)

`anygarden-frontend` is a Vite SPA with **no library `dist/`**, so the converter can't bundle a
published entry. Instead:

- **Bundle entry** = a hand-written barrel `packages/cluster/frontend/ds-entry.tsx` (re-exports only
  the `ui/` primitives), passed via `cfg.entry` / `--entry`. It MUST live **inside the frontend
  package** — with `--entry`, the converter derives `PKG_DIR` by walking up to the nearest named
  `package.json`, so a barrel under `.design-sync/` would resolve `PKG_DIR` to the repo root and break
  every package-relative path. Don't move it out of the package.
- **Component list** = `cfg.componentSrcMap` (15 entries). With no shipped `.d.ts`, the export scan is
  empty, so `componentSrcMap` is the sole source of the component set + their src paths.
- **`@/` alias** resolves via `cfg.tsconfig` (`tsconfig.json` → `"@/*": ["./src/*"]`).

## Build inputs the converter can't produce — `bash .design-sync/build-assets.sh`

`cfg.buildCmd` = `bash .design-sync/build-assets.sh`. It regenerates two things into
`packages/cluster/frontend/dist/` (gitignored):

1. **`dist/ds-styles.css`** (`cfg.cssEntry`) — the compiled Tailwind v4 stylesheet. Produced by the
   REAL `vite build` (faithful), then: prepend the Inter `@import` (Google Fonts, as `index.html`
   does); append bare-token aliases (`--input`/`--ring`/`--muted-foreground` → the `--color-*`
   versions that ChatInput references); append the **full `@theme` palette as an explicit `:root`**
   (Tailwind tree-shakes unused theme vars, but a DS must ship its whole vocabulary); add `.text-display`
   + `.surface-alt` (two `@utility` classes nothing scanned used, so Tailwind never emitted them).
2. **`dist/types/`** — component `.d.ts` via `tsc` (ephemeral `tsconfig.ds-dts.json`). `findTypesRoot`
   picks up `dist/types`. Without this the props bodies are empty.

## Render check

`playwright@1.61.0` (pins chromium **1228**, which is cached at `~/.cache/ms-playwright/`). The repo
itself has no playwright; it's installed into `.ds-sync/` (gitignored). If the cache changes, match the
playwright version to a cached `chromium-<build>` (1.60.0→1223, 1.61.0→1228).

## Known render warns (re-syncs: anything NOT here is new — look at it)

- `[TOKENS_MISSING]` 2 vars, below threshold — `--color-surface-muted` + one other are referenced by
  **app screens compiled into the shared Tailwind CSS**, not by any synced primitive. Harmless.
- `[FONT_REMOTE] "Inter"` — Inter loads at runtime from the Google Fonts `@import`. Expected, no action.

## Re-sync risks (what can silently go stale)

- **The driver does NOT run `cfg.buildCmd`.** `resync.mjs` runs `package-build.mjs` directly. So when
  the frontend source changes, **run `bash .design-sync/build-assets.sh` FIRST**, then the driver —
  otherwise the bundle ships a stale CSS/`.d.ts`. (When in doubt, run it; output is deterministic.)
- **`cfg.cssEntry` and `findTypesRoot` point at generated files** under `dist/` (gitignored, not in the
  repo). A fresh clone has no `dist/` until `build-assets.sh` runs.
- **`build-assets.sh` appends the full `@theme` block via `awk` from `src/index.css`.** New tokens added
  to `@theme` flow through automatically; tokens RENAMED or moved out of `@theme` would silently drop
  from the shipped palette. If a component starts using a NEW bare (non-`--color-`) token, add an alias
  line like the existing `--input`/`--ring` ones.
- **Inter is fetched remotely** at render time. Offline/locked-down environments render in the system
  fallback.
- **The conventions header (`conventions.md`) names concrete tokens/classes.** It was validated against
  this build; if `index.css` `@theme` changes, re-validate the header (the conventions-header step does
  this automatically on re-sync).
- New `ui/` components are NOT auto-discovered (export scan is empty) — add them to `cfg.componentSrcMap`
  AND to the `ds-entry.tsx` barrel.

## QA pass (multi-agent audit) — what was hand-fixed and what's left

A 54-agent QA audit ran after the first upload. Fixes applied to the synced surface:

- **`cfg.dtsPropsFor` is hand-maintained for 13 components.** The extractor curated inline-typed
  components (`React.HTMLAttributes`/`ComponentPropsWithoutRef` in the forwardRef generic, not a named
  `interface XProps`) down to an opaque `[key:string]:unknown`, and dropped HTML handlers from Button.
  `dtsPropsFor` now pins accurate props (Button onClick/disabled/type/aria-label, Input/Label/Separator/
  Tabs/ChatInput real props, controlled-state props for Dialog/Tabs, etc.). **If a component's real API
  changes, update its `dtsPropsFor` entry** — it OVERRIDES extraction, so a stale entry silently ships a
  wrong contract. Badge + MessageLoading are left to extraction (Badge has a clean named interface;
  MessageLoading takes no props).
- **Compound parts are documented in `conventions.md`, not typed per-part.** The emitter only emits a
  `.d.ts` for the listed (carded) component, so CardHeader/TableRow/DialogContent/AvatarFallback/etc. are
  on `window.AnygardenUI` and shown in previews + the conventions header, but have no standalone
  `<Part>Props`. Acceptable (they're simple div/text wrappers); revisit if the design agent misuses them.

## Re-sync risks (continued) — source-level a11y the audit flagged (NOT fixed; app-source territory)

These are real accessibility gaps in the **app source** (`src/components/ui/chat/`), out of scope for a
design sync (don't edit product code from here). Worth a separate app-side fix:

- `ChatBubbleAvatar` hardcodes `alt="Avatar"` with no override prop (chat-bubble.tsx) — every chat avatar
  announces a meaningless label.
- `ChatBubbleAction` (icon button) and `ChatBubbleActionWrapper` are exported on the bundle but not carded
  (not in `componentSrcMap`), so they have no `.d.ts`/`.prompt.md`. The conventions header notes they need
  `aria-label`. Either card them or stop exporting if not meant for consumers.
- `MessageLoading` SVG has no `role`/`aria-label`/`aria-live`; the `isLoading` bubble announces nothing to
  screen readers.
