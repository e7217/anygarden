/**
 * Deterministic avatar tone + initials derivation.
 *
 * Given an entity's id (or any stable seed) we pick a tone from a
 * fixed palette so the same entity renders the same background
 * across every view that mounts an ``<EntityAvatar/>``. The palette
 * is drawn from ``DESIGN.md`` (§2) — warm-neutral + semantic soft
 * accents — and intentionally stops at 8 slots: more would force
 * us to reach for saturated colors that don't belong in the Notion
 * warm-neutral aesthetic.
 */

export interface AvatarTone {
  /** Background color (soft tint over white). */
  bg: string
  /** Foreground (text/glyph) color. */
  fg: string
  /** Translucent hue for focus rings / presence accents. */
  ring: string
}

// ``color-mix`` references come from Tailwind v4's palette — same
// function is already used elsewhere (e.g. ParticipantListPopover
// role badges). Keeping the values inline here instead of reaching
// for CSS variables because these are seed-driven and shouldn't be
// themable through the theme layer.
const PALETTE: readonly AvatarTone[] = Object.freeze([
  // 1. warm-neutral — the "default" tone. Closest to surface-alt.
  {
    bg: '#efedea',
    fg: '#615d59',
    ring: 'rgba(97,93,89,0.20)',
  },
  // 2. teal (success-soft)
  {
    bg: 'color-mix(in srgb, #2a9d99 18%, white)',
    fg: '#1d6f6c',
    ring: 'rgba(42,157,153,0.25)',
  },
  // 3. green
  {
    bg: 'color-mix(in srgb, #1aae39 18%, white)',
    fg: '#0d7821',
    ring: 'rgba(26,174,57,0.25)',
  },
  // 4. orange (warning accent — tinted enough that it does not
  //    read as a warning badge on its own)
  {
    bg: 'color-mix(in srgb, #dd5b00 18%, white)',
    fg: '#a33f00',
    ring: 'rgba(221,91,0,0.25)',
  },
  // 5. pink (decorative)
  {
    bg: 'color-mix(in srgb, #ff64c8 18%, white)',
    fg: '#b03c91',
    ring: 'rgba(255,100,200,0.25)',
  },
  // 6. purple (premium)
  {
    bg: 'color-mix(in srgb, #391c57 14%, white)',
    fg: '#391c57',
    ring: 'rgba(57,28,87,0.25)',
  },
  // 7. brown (earthy)
  {
    bg: 'color-mix(in srgb, #523410 14%, white)',
    fg: '#523410',
    ring: 'rgba(82,52,16,0.25)',
  },
  // 8. notion-blue — reuses the app's primary accent. Shows up
  //    roughly 1 in 8 picks so it stays special without dominating.
  {
    bg: '#f2f9ff',
    fg: '#005bab',
    ring: 'rgba(0,117,222,0.25)',
  },
])

export const PALETTE_SIZE = PALETTE.length

/**
 * FNV-1a 32-bit hash → palette index.
 *
 * FNV-1a is cheap, has acceptable distribution for short strings
 * (UUIDs, display names), and does not require any Web Crypto
 * plumbing — which matters here because this function is called
 * on every ``<EntityAvatar/>`` render.
 */
export function getAvatarTone(seed: string): AvatarTone {
  if (!seed) return PALETTE[0]
  let hash = 2166136261 // FNV offset basis
  for (let i = 0; i < seed.length; i++) {
    hash ^= seed.charCodeAt(i)
    // Math.imul preserves 32-bit multiplication semantics
    // (regular ``*`` would overflow silently into a double).
    hash = Math.imul(hash, 16777619)
  }
  const idx = (hash >>> 0) % PALETTE_SIZE
  return PALETTE[idx]
}

// Hangul syllables + CJK unified ideographs cover Korean, Chinese
// and Japanese kanji. Hiragana/Katakana are intentionally excluded —
// for a Japanese name like "たなか", the single-char initial would
// not help identification; we'd want "T" from a romanization, but
// that's out of scope. Keeping the rule narrow.
const CJK_REGEX = /[\u3400-\u9fff\uac00-\ud7af]/

export function getInitials(name: string): string {
  const trimmed = (name ?? '').trim()
  if (!trimmed) return '?'
  const tokens = trimmed.split(/\s+/).filter(Boolean)
  if (tokens.length === 0) return '?'
  const first = tokens[0]
  // If the first token starts with a CJK character, the entire
  // name is likely a CJK name — one character is the conventional
  // abbreviation (e.g., "김수현" → "김"). Don't concatenate with
  // the last token.
  if (CJK_REGEX.test(first.charAt(0))) {
    return first.charAt(0)
  }
  if (tokens.length === 1) {
    return first.charAt(0).toUpperCase()
  }
  const last = tokens[tokens.length - 1]
  return (first.charAt(0) + last.charAt(0)).toUpperCase()
}
