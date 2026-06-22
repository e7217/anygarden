# chore(design-sync): import UI primitives to claude.ai/design

- Commit: `4e6ca69` (4e6ca69d6d818463825894dc56b9e74f2e8f2f2c)
- Author: Changyong Um
- Date: 2026-06-22T14:08:59+09:00
- PR: —

## Situation

Claude Design (claude.ai/design) builds UI from generic components out of the box. To make it
build with Anygarden's actual look-and-feel, the design system has to be converted into the format
Claude Design consumes and uploaded. Anygarden's design system lives in
`packages/cluster/frontend/src/components/ui/` (shadcn/Radix primitives, Tailwind v4, the Notion
warm-neutral palette from `DESIGN.md`) — but the frontend is a **Vite SPA, not a published component
library**, so there is no `dist/` entry or shipped `.d.ts` tree for the `/design-sync` converter to
bundle from. This commit captures the durable inputs that made a high-fidelity import possible.

## Task

- Scope the sync to the reusable `ui/` primitives only (app screens depend on routing/websocket/API
  context and won't render standalone).
- Give the converter a bundle entry, a component list, compiled Tailwind CSS, and a `.d.ts` tree —
  none of which the app produces natively.
- Author and verify a rich preview per component, and a token-vocabulary header for the design agent.
- Commit only re-sync inputs; keep all generated artifacts out of git.

## Action

Added under `.design-sync/` plus one barrel in the frontend package (621 insertions, 21 files):

- `packages/cluster/frontend/ds-entry.tsx` — barrel re-exporting only the 15 `ui/` primitives; passed
  as the converter `--entry`. Lives inside the package so `PKG_DIR` resolves to the frontend (the
  converter walks up from the entry to the nearest named `package.json`).
- `.design-sync/config.json` — `shape: package`, `componentSrcMap` (15 components → src paths),
  `cssEntry: dist/ds-styles.css`, `tsconfig` for `@/` alias resolution, `Dialog` overlay override
  (`cardMode: single`), `readmeHeader`, and the pinned `projectId`.
- `.design-sync/build-assets.sh` (`cfg.buildCmd`) — runs the real `vite build` to extract the compiled
  Tailwind stylesheet, prepends the Inter `@import`, aliases the bare `--input`/`--ring`/
  `--muted-foreground` tokens ChatInput uses, injects the full `@theme` palette as `:root` (Tailwind
  tree-shakes unused vars), and emits component `.d.ts` via `tsc` into `dist/types/`.
- `.design-sync/previews/*.tsx` — 15 authored preview compositions (realistic anygarden domain
  content), each rendered/graded `good` on the absolute rubric.
- `.design-sync/conventions.md` — token-vocabulary + setup header prepended to the uploaded README.
- `.design-sync/NOTES.md` — re-sync gotchas (driver doesn't run `buildCmd`; generated `dist/` inputs)
  and a Re-sync risks section.
- `.gitignore` — ignores `ds-bundle/`, `.ds-sync/`, `packages/cluster/frontend/dist/`,
  `.design-sync/.cache|learnings|node_modules`.

The 15 components + bundle + compiled CSS + verification anchor were uploaded to the "Anygarden Design
System" project (`ceb6b73e-802b-4526-b94c-69d3c68bf4fc`); those outputs are not in git.

## Decisions

- **Barrel entry + `componentSrcMap` over synth-entry mode.** The converter's no-dist fallback
  `export *`s every `src/` file, which would pull the whole app onto `window.AnygardenUI` and bloat/
  pollute the bundle. A hand-written barrel scopes the bundle surface to the `ui/` primitives exactly.
  Rejected: building a real library dist (tsup/vite-lib) — heavier toolchain, and the app type-checks
  could block emit.
- **Compiled CSS from the real `vite build`, shipped with the full `@theme` palette.** The components
  use Tailwind utility classes + CSS-var tokens, so raw `src/index.css` (`@import "tailwindcss"`) would
  ship unusable. Using the production build is faithful; but Tailwind v4 tree-shakes unused theme vars,
  so the whole `@theme` is injected as an explicit `:root` — a design system must expose its entire
  token vocabulary to designs, not just what the app happened to use.
- **`.d.ts` via `tsc` over hand-written `dtsPropsFor`.** Scoped declaration emit gives accurate
  inherited HTML attrs + the cva variant/size unions automatically; hand-writing 15 prop bodies was the
  rejected alternative (tedious, lossy).
- **Token aliases for `--input`/`--ring`/`--muted-foreground`.** ChatInput references bare tokens the
  `@theme` never defined (only `--color-*` versions exist) — a latent app gap. Aliased in the shipped
  CSS so the primitive renders with a visible border; left the app source untouched.
- Assumption to revisit: the driver (`resync.mjs`) does **not** run `cfg.buildCmd`, so a future re-sync
  must run `build-assets.sh` first when frontend source changes — recorded in NOTES.md.

## Result

15 primitives imported (Button, Card, Table, Badge, Avatar, Dialog, Input, Label, Separator, Tabs,
ScrollArea, ChatBubble, ChatInput, ChatMessageList, MessageLoading), all with authored previews graded
`good`, render check 15/15 clean, `package-validate` exit 0. The Claude Design agent now builds with
Anygarden's real components. Re-sync inputs are committed and reproducible; generated artifacts are
gitignored. Pending: none — the upload is anchored (`_ds_sync.json`).
