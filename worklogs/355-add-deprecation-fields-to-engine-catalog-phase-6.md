# feat(cluster): add deprecation fields to engine catalog (Phase 6) (#355)

- Commit: `88bb941`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #355

## Situation

Phases 0–4 of #355 brought the OpenHands engine to feature parity
with claude-code, codex, and gemini-cli (adapter scaffold, MCP
wiring, skills awareness, DelegateTool, full provider catalog).
Phase 5 captured the validation plan in
`docs/decisions/005-openhands-validation-plan.md` with four
explicit decision criteria that have to hold before CLI engines
can be flagged as legacy. The migration plan's Phase 6 is the
*infrastructure* needed for that flagging — adding the schema
fields and helper API the admin UI / API will read from once
validation results land.

The migration plan's tracking scope (#355 issue body) explicitly
excludes the actual CLI engine *removal*. Phase 6 stops at
"infrastructure ready, no engines flipped yet"; flipping happens
in a future PR after operator-driven validation runs append
results to the decisions doc.

## Task

Land the deprecation infrastructure in the catalog without
flipping any currently-shipped engine. Constraints:

- New fields must default such that pre-#355 behaviour stays
  unchanged for every consumer (admin UI, API responses,
  validators).
- Field docstrings must explicitly point at the Phase 5 decision
  criteria so a future operator can't flip the flag without
  re-reading the validation plan.
- A small helper (`is_deprecated(engine)`) for callers that need
  a yes/no rather than reading the full entry, with a
  caller-friendly default for unknown engines.
- Tests must lock the pre-validation invariant ("nothing is yet
  flagged") so a premature flip is caught during code review,
  not at runtime.

## Action

- `packages/cluster/doorae/engines/catalog.py`:
  - `EngineCatalogEntry` grew two optional fields:
    - `deprecated: bool = False` — admin UI sort key + 'legacy'
      badge signal. Field docstring spells out that it's a *UX
      hint*, not a runtime gate; agents already pinned to a
      deprecated engine keep running. Pre-flip explanation
      references Phase 5's decision criteria so the next operator
      can't bypass validation.
    - `deprecation_note: Optional[str] = None` — one-line human
      rationale shown alongside the legacy badge, populated when
      `deprecated=True` is set later. Currently `None` for every
      entry.
  - `is_deprecated(engine)` helper returns `False` for unknown
    engines so call-site predicates (`if is_deprecated(...): ...`)
    stay safe without an extra existence check.
- `packages/cluster/tests/test_engine_catalog.py`:
  - `TestDeprecationFields` (4 tests):
    - `test_default_deprecated_false_for_all_engines` — locks the
      pre-validation invariant. The assertion message points at
      the decisions doc so a future PR that flips the flag without
      validation results gets a loud failure with the right
      pointer.
    - `test_default_deprecation_note_none` — same invariant for
      the note field.
    - `test_is_deprecated_helper` — both unknown-engine path and
      every catalog entry returning `False`.
    - `test_entry_can_carry_deprecation_metadata` — frozen
      dataclass round-trips the new fields. Belt-and-suspenders
      against an accidental field removal in a future refactor.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 6
asked for "CLI 엔진 deprecation 마킹" — concretely, marking
claude-code / codex / gemini-cli as `deprecated=True` in the
catalog. Three options were on the table for *what to ship in
this commit*:

- **Mark CLI engines deprecated immediately** (rejected). Phase 5
  validation hasn't run; the four decision criteria in the
  decisions doc aren't satisfied; flipping now would force a UX
  transition without the empirical case for migration on record.
  The whole reason Phase 5 has explicit criteria is to avoid this.
- **Skip Phase 6 entirely until validation completes** (rejected).
  The infrastructure (schema fields, helper) is mechanical and
  doesn't depend on validation outcomes. Landing it now lets a
  validation-completion PR flip a flag instead of also
  introducing new fields, keeping the post-validation diff
  minimal and reviewable.
- **Land the schema + helper, leave every flag at False** (chosen).
  Decouples the schema landing from the flag flip. The flag flip
  becomes a small one-PR change once `docs/decisions/005-openhands-
  validation-plan.md` shows results clearing the criteria.

What tipped the scale: separating "schema ready" from "flag
flipped" makes the validation-driven decision auditable. The
flip PR will be a one-line catalog change plus a worklog citing
the validation results — anyone reviewing it can see the
empirical case in the decisions doc.

What I explicitly didn't include:

- Actually flipping `deprecated=True` on any CLI engine. Pre-
  validation flipping is exactly what the four decision criteria
  guard against.
- API exposure of the new fields (e.g. `/api/v1/engines/...`
  response field). The dataclass fields propagate through any
  serialiser that introspects the dataclass, but if the API
  uses an explicit response model that doesn't include the new
  fields, it stays as-is. A frontend PR that needs to render the
  badge can opt into the field then; surfacing it server-side
  before that has any consumer would be premature.
- Frontend 'legacy' badge component / sort logic. Tracked on the
  frontend side (#346 lineage) and gated on actual flipping
  happening; rendering a sort hint that nothing currently uses
  would just add a styling surface to maintain.

Assumptions worth flagging if they break later:
- The `deprecated` flag stays a UX hint, not a runtime gate. If
  a future iteration decides "deprecated also means new agents
  can't pick this engine", the agent-creation API would need a
  validation step that reads `is_deprecated`. Adding that gate is
  a separate decision; documenting it here so a future maintainer
  doesn't conflate the two layers.
- The `deprecation_note` is single-line. If a future flip wants
  multi-paragraph rationale, the field type would change to
  something richer (markdown? structured object?) and the helper
  would need updating. Current shape is intentional minimum.
- Pre-validation invariant ("all engines `False`") is enforced by
  the `test_default_deprecated_false_for_all_engines` test. After
  validation results land in the decisions doc and a follow-up PR
  flips claude-code/codex/gemini-cli, that test needs updating to
  pin the *new* expected state (the failure message in the
  current assertion already tells the future maintainer where to
  look).

## Result

Phase 6 ships the deprecation infrastructure: schema fields,
helper, and pre-validation invariant tests. Nothing changes for
any currently-shipped engine — every entry keeps
`deprecated=False` until Phase 5 validation runs.

Coverage: 15 / 15 cluster `engine_catalog` tests pass (4 new for
Phase 6 on top of the existing 11). The full agent suite (363
tests) and the cluster MCP-adjacent suite (38 tests) stay green.

This closes the implementation surface for #355's tracking scope.
The remaining work is non-code:

1. Operator-driven runtime validation per
   `docs/decisions/005-openhands-validation-plan.md`.
2. Append "Results — \<date>" to that doc as runs complete.
3. Once results clear the four decision criteria, a small
   follow-up PR flips claude-code / codex / gemini-cli to
   `deprecated=True` with an appropriate `deprecation_note`.
4. Eventually, a separate issue tracks actual CLI adapter
   removal — out of scope for #355.
