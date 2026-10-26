# fix(campaigns): delimit the learnings section with markers and refuse text that would leave it (#930)

- Commit: `7b3dd63` (7b3dd638164b805584f67eae9ce4601939501743)
- Author: Changyong Um
- Date: 2026-10-26T16:05:56+09:00
- PR: #930

## Situation

The `## Campaign learnings` section in AGENTS.md was delimited only by the
next level-1/2 heading in the file. A naïve regex (`_SECTION_BREAK`) was
used to find the section boundary, which missed edge cases such as headings
inside fenced code blocks, indented ATX headings, and setext-style headings.
Equally, the body-validation logic (`_check_section_body`) only checked for
the same naïve pattern, so certain heading forms could escape detection and
silently corrupt the file structure.

## Task

- Introduce explicit HTML comment markers (`<!-- campaign-learnings:start -->`
  / `<!-- campaign-learnings:end -->`) around the learnings body so the
  replacement is anchor-based rather than scan-based.
- Upgrade the body validator to reject: level-1/2 ATX headings (up to three
  leading spaces), setext underlines, unclosed code fences, and the marker
  lines themselves.
- Migrate existing marker-less sections on first write (detect absence of
  markers, find the end with the improved scanner, rewrite with markers).
- Export the new constants and the renamed `check_section_body` helper so
  tests and callers can reference them.
- Update `docs/campaigns.md` to document the new marker-based constraint.

## Action

**`packages/cluster/anygarden/campaigns/retrospective.py`**

- Added `LEARNINGS_START` / `LEARNINGS_END` constants (HTML comment markers).
- Replaced `_SECTION_BREAK` regex with two precise patterns: `_TOP_ATX`
  (ATX headings at levels 1–2, up to three leading spaces) and `_SETEXT`
  (setext underlines); added `_FENCE` for code-fence detection.
- Added `_scan(lines)` helper that classifies each line as inside/outside a
  fence, collects indices where level-1/2 headings start (ATX or setext),
  and reports unclosed fences.
- `replace_learnings_section`: now first looks for an existing `LEARNINGS_START`
  marker and replaces only the content between the markers. Falls back to
  the improved scanner for legacy (marker-less) sections, then wraps the new
  body with markers. Appending a new section also wraps it with markers.
- Renamed `_check_section_body` → `check_section_body` (public); rewrote it
  to use `_scan`, refusing marker lines, any top-level heading, and unclosed
  fences.
- Updated `__all__` to export `LEARNINGS_START`, `LEARNINGS_END`, and
  `check_section_body`.

**`packages/cluster/tests/test_campaigns_retrospective.py`**

- Imported `LEARNINGS_START`, `LEARNINGS_END`, `check_section_body`, and
  `CampaignToolError`.
- Added `_section(body)` helper that wraps body with heading + markers.
- Updated existing tests to expect marker-wrapped output.
- Added new parametrized tests:
  - `test_only_the_text_between_the_markers_is_replaced` — confirms only
    content between markers changes.
  - `test_a_marker_less_section_is_migrated` — legacy section gets markers.
  - `test_a_marker_less_section_ends_at_any_top_level_heading` — parametrized
    over indented ATX and both setext forms.
  - `test_a_marker_less_section_ignores_headings_inside_code_fences`.
  - `test_section_text_that_would_leave_the_section_is_refused` — parametrized
    over six invalid body strings.
  - `test_section_text_with_sub_headings_fences_and_rules_is_fine` — parametrized
    over three valid body strings.
- Extended `test_a_learning_is_undone_with_the_revision_restore` to assert
  revision history rows carry the expected `source` and `context.rationale`.

**`docs/campaigns.md`** (line 67): updated the bullet describing the
learnings-section constraint to mention the new markers and the refusal of
top-level headings and unclosed code fences.

## Decisions

- **Marker-based vs. pure scan**: A scan-based approach requires a correct
  CommonMark-aware parser for every edge case (fenced code, indented headings,
  setext). Embedding stable HTML comment markers as anchors eliminates the
  scan entirely for files that have already been written with this version,
  making the replacement O(n) and unambiguous. The scanner is still needed
  for migration of legacy files and for body validation, but it is no longer
  on the critical path.
- **Migration on first write vs. one-time migration script**: A migration
  script would require a deployment step and risk partial application across
  agents. Writing markers on the first replacement is self-healing and
  requires no coordination.
- **Renaming `_check_section_body` to `check_section_body`**: The function
  is now tested directly and referenced in the `__all__` export; keeping it
  private would have required either test-private access patterns or
  duplication. Making it public is the cleaner contract.
- **Setext detection**: The existing `_SECTION_BREAK = re.compile(r"^#{1,2}\s")`
  silently ignored setext headings entirely. The new `_SETEXT` check looks
  one line back, which matches the CommonMark spec without a full block
  parser.

## Result

- AGENTS.md learnings sections are now written with stable HTML comment
  markers, making future replacements anchor-based and immune to heading
  detection errors.
- The body validator rejects level-1/2 headings (ATX indented up to 3 spaces,
  setext), unclosed code fences, and the marker lines themselves, preventing
  structural corruption of AGENTS.md.
- Existing marker-less sections are migrated transparently on next write.
- 15 new/updated test cases cover the replacement and validation logic across
  all edge cases.
- `docs/campaigns.md` documents the new markers and refusal rules for
  campaign authors.
