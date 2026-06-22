#!/usr/bin/env bash
# Regenerate the two build inputs the /design-sync converter can't derive from
# this app itself (anygarden-frontend is a Vite SPA, not a library with a dist):
#   1. compiled Tailwind v4 stylesheet  → cfg.cssEntry  (dist/ds-styles.css)
#   2. component .d.ts tree              → findTypesRoot (dist/types/)
# Everything lands under packages/cluster/frontend/dist/ (gitignored).
# Run from the repo root.  Referenced by cfg.buildCmd so re-sync regenerates it.
set -uo pipefail
FE="packages/cluster/frontend"
DIST="$FE/dist"
rm -rf "$DIST"
mkdir -p "$DIST/types"

# 1. Compiled Tailwind CSS ----------------------------------------------------
# Run the real production Vite build (faithful to what ships) into a throwaway
# dir, take the main entry stylesheet (index-*.css — the theme tokens + every
# scanned utility; the TopologyPage chunk is @xyflow-only and irrelevant), and
# prepend the Inter @import the app loads from Google Fonts (see index.html) so
# the bundle is self-contained.
TMP="$(mktemp -d)"
( cd "$FE" && npx vite build --outDir "$TMP" --emptyOutDir ) >/dev/null 2>&1
CSS="$(ls "$TMP"/assets/index-*.css 2>/dev/null | head -1)"
if [ -z "$CSS" ]; then echo "ERROR: no compiled index-*.css produced" >&2; exit 1; fi
{
  echo "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');"
  cat "$CSS"
  # Alias the bare shadcn-style tokens a few components reference (ChatInput uses
  # --input/--ring/--muted-foreground) onto the prefixed @theme tokens that are
  # actually defined. Without these, those components render with a missing
  # border / undefined focus ring. --color-surface-muted is referenced by app
  # screens only (not the synced primitives) but aliased here to keep the
  # token closure clean.
  echo ":root{--input:var(--color-input);--ring:var(--color-ring);--muted-foreground:var(--color-muted-foreground);--color-surface-muted:var(--color-surface-alt);}"
  # Ship the FULL @theme token palette as an explicit :root block. Tailwind v4
  # tree-shakes unused theme vars out of its compiled output, but a design system
  # must expose its whole token vocabulary (radius-xl, space-12, shadow-focus, …)
  # so designs built in claude.ai/design can reference any documented token.
  echo ":root{"; awk '/^@theme/{f=1;next} f&&/^}/{f=0} f{print}' "$FE/src/index.css"; echo "}"
  # The two @utility classes the docs name but no scanned source used (so Tailwind
  # never emitted them): make them concrete so className="text-display"/"surface-alt" work.
  echo ".text-display{font-size:3rem;line-height:1.05;letter-spacing:-.02em;font-weight:700}"
  echo ".surface-alt{background-color:var(--color-surface-alt)}"
} > "$DIST/ds-styles.css"
rm -rf "$TMP"
echo "css: $DIST/ds-styles.css ($(du -h "$DIST/ds-styles.css" | cut -f1))"

# 2. Component .d.ts ----------------------------------------------------------
# Emit declarations for src/components/ui only (+ the cn() helper it imports).
# Ephemeral tsconfig; tsc may report type errors but still emits (noEmitOnError
# defaults false), so don't gate on its exit code.
cat > "$FE/tsconfig.ds-dts.json" <<'JSON'
{
  "extends": "./tsconfig.json",
  "compilerOptions": {
    "noEmit": false,
    "declaration": true,
    "emitDeclarationOnly": true,
    "outDir": "dist/types",
    "rootDir": "src",
    "skipLibCheck": true
  },
  "include": ["src/components/ui/**/*", "src/lib/utils.ts"]
}
JSON
( cd "$FE" && npx tsc -p tsconfig.ds-dts.json ) >/dev/null 2>&1
rm -f "$FE/tsconfig.ds-dts.json"
echo "dts: $(find "$DIST/types" -name '*.d.ts' | wc -l) .d.ts files"
