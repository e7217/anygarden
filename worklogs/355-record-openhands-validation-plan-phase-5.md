# docs(decisions): record OpenHands validation plan (Phase 5) (#355)

- Commit: `30dc6ad`
- Author: Changyong Um
- Date: 2026-05-09
- PR: #355

## Situation

Phases 0–4 of #355 wired the OpenHands V1 SDK as a fourth engine
adapter and brought it to feature parity with the three CLI
engines (claude-code, codex, gemini-cli) on adapter scaffold,
MCP exposure, skills awareness, DelegateTool, and the full
provider × model catalog. The migration plan's Phase 5 calls for
empirical validation that the new engine actually delivers the
structural improvements the migration promised — task-transition
recognition, idle/abort detection, MCP exposure stability —
without regressing on dimensions the CLI engines handle well
(streaming latency, multi-turn coherence). Without that validation
on record, Phase 6 (deprecation marking) has no defensible basis,
and a future maintainer would have to re-derive whether openhands
is actually better or just newer.

## Task

Land the validation **plan** in this PR — the actual measurements
require live LLM API calls and operator-driven scenarios that this
PR can't perform. Constraints:

- The plan has to spell out concrete scenarios with explicit
  setup, what-we-measure, and why-this-matters notes — vague
  "compare them" handwaving here would let a future operator
  cherry-pick metrics.
- It needs explicit decision criteria for Phase 6 so flipping
  CLI engines to `deprecated=True` isn't a judgment call.
- Result recording shape has to be append-only so the validation
  trajectory stays visible across multiple operator runs.
- Live the doc under `docs/decisions/` with the existing 001–004
  ADRs so it gets the same archival weight.

## Action

- `docs/decisions/005-openhands-validation-plan.md` (new):
  - **Context** section — Phases 0–4 done, Phase 5 = compare,
    can't run live LLMs in this PR.
  - **Five scenarios** spelled out:
    - A: task transition recognition (new prompt mid-reply)
    - B: idle / execution-state detection (long MCP tool call)
    - C: abort responsiveness (HUP-equivalent during long
      generation)
    - D: streaming stability under long output (inter-token
      latency + stalls)
    - E: MCP exposure regression of #352 / #354 (50 invocations
      of a doorae cluster MCP tool per engine)
  - **Model parity** recipe pinning each engine to a comparable
    model from the Phase 4 catalog — claude-code on
    `claude-sonnet-4-6`, codex on `gpt-5.4`, gemini-cli on
    `gemini-3-pro-preview`, openhands rotated across all three
    providers so engine-vs-model effects don't conflate.
  - **Metrics summary table** template with `_tbd_` placeholders
    so a follow-up PR or operator note fills in measurements.
  - **Decision criteria** — four bullets that must hold
    simultaneously for Phase 6 deprecation: (1) openhands matches
    or beats every CLI engine on at least one metric per
    scenario; (2) streaming p95 within 2× best CLI; (3) no
    scenario fails > 5% of trials; (4) MCP holds over 50
    invocations (the explicit #352 regression guard).
  - **Recording results** template that's append-only so a
    future maintainer sees the full validation trajectory rather
    than just the last state.
  - **Out of scope** — cost / FinOps, frontend UX, sub-agent
    streaming integration. Keeps the validation focused.
  - **Related** links to plan, all five worklogs, both the #352
    and #354 PRs, and the Phase X roadmap doc.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 5
asked for "정성/정량 검증 시나리오 + 결과 문서화". Three options
for what to ship in *this* PR were on the table:

- **Run actual validations and ship the results** (rejected
  because impossible from this PR — no live LLM access, no
  operator-controlled environment, no agreed-upon model parity
  pricing).
- **Skip Phase 5 entirely until validation can run** (rejected
  because the migration plan asks for both the scenario design
  and the result recording, and the design itself is a non-
  trivial deliverable that benefits from being captured at the
  same time the implementation is fresh).
- **Ship the plan + an empty results template** (chosen). The
  scenarios get captured while the implementation context is
  fresh; the results template stays empty until live runs
  happen, at which point an operator appends a `## Results —
  <date>` section. The append-only shape preserves the validation
  trajectory.

What tipped the scale: the alternative to writing the plan now
isn't "no plan" — it's "ad-hoc scenarios discovered on the fly
during deprecation". That always favors the engine the operator
remembers best, which would be the CLI engines. Locking in
scenarios now means the validation is reproducible and the
deprecation decision is defensible.

What I explicitly didn't include:

- Real metric numbers, even illustrative ones. Putting a fake
  number in the table would invite confusion about whether a
  validation actually ran. `_tbd_` markers force someone to
  realise nothing has been measured yet.
- A specific date for when validation should complete. The
  bottleneck is operator availability + budget for live LLM
  calls; setting a hard date here would either be ignored or
  force corner-cutting.
- A FinOps comparison. The migration's value isn't a cost win —
  it's structural. Cost is tracked elsewhere; mixing the two
  would obscure the structural argument.

Assumptions worth flagging if they break later:
- The four decision criteria bullets are calibrated for
  "openhands is at least as good as CLI engines, ideally better
  on the structural axes". If validation reveals openhands is
  uniformly better, the criteria over-protect (Phase 6 still
  gets the green light); if openhands is worse on a dimension
  not covered by the criteria, the criteria under-protect and
  need a fifth bullet. The append-only Results section gives a
  future maintainer the data to recalibrate.
- Three openhands runs (one per provider) is the right
  granularity for engine-vs-model isolation. If we discover
  larger inter-provider variance than inter-engine variance, the
  comparison shape may need rotating roles (compare-by-model
  rather than compare-by-engine).

## Result

Phase 5 ships as a planning artifact. The validation **scenarios
and decision criteria** are now on record so a future operator
can run the comparison and append results to a stable structure.
No code changed; no runtime behavior changed.

Phase 6 (CLI engine deprecation marking) is now unblocked
*procedurally* — the doc spells out exactly what has to be true
before flipping the flag. Whether Phase 6 lands in this PR or a
follow-up depends on whether validation results land before
merge.

Still pending: actual validation runs (operator + live LLM
budget), Phase 6 deprecation marking once results clear the
decision criteria.
