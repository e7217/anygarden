# feat(goals): make the token budget optional and off by default (#963)

- Commit: `4649989`
- Author: (see git log)
- Date: 2026-10-28
- PR: #963

## Situation

Every goal required a `budget.max_total_tokens` value at creation (CMP-01). In practice the number had no defensible basis — the system couldn't enforce it mid-cycle anyway (a 15,000-token budget regularly resulted in 546,237 tokens consumed). Worse, once the budget was exhausted the system refused to run a retrospective, blocking the normal close-out of the goal entirely. The token budget had become an obstacle rather than a guardrail.

## Task

Make `budget.max_total_tokens` optional everywhere it appeared:

- Policy: accept a goal with no budget; `usable_tokens()` returns `None` (unlimited).
- Scheduler, lifecycle, and execution limits: skip token checks when there is no budget.
- Extend API: refuse a token raise if no budget exists (`NO_TOKEN_BUDGET`); a missing budget must not block reopening.
- Edit/PATCH: `budget.max_total_tokens: null` removes the budget; if the goal was `budget_exhausted` at the time, reopen it via the existing extend path.
- API schemas: `BudgetIn.max_total_tokens` optional, `CampaignCreate.budget` optional, `usable_tokens` response field nullable.
- `propose_goal` MCP tool: remove budget from required fields.
- Brief: show budget line only when a budget exists; retro stats show raw tokens used without a budget.
- UI: fold token budget under "Advanced" (collapsed by default); right rail shows tokens used without a budget and hides the extend-budget input when there is none.
- Docs: update CMP-01 (budget optional) and CMP-10 (scope to goals that have a budget).

Constraint: no DB migration — existing rows already carry `max_total_tokens`, so behaviour for budgeted goals must be unchanged.

## Action

**Policy (`campaigns/policy.py`):**
- `_budget()`: accepts `None`, `{}`, or missing `max_total_tokens`; validates only when a value is provided; returns dict with optional `max_total_tokens`.
- `usable_tokens()`: return type changed to `int | None`; returns `None` when no budget.
- New helper `has_token_budget(budget)` to avoid repeating the same guard at every call site.
- `cycle_tokens_spent()` helper added for consistent accounting.

**Scheduler (`campaigns/scheduler.py`, `campaigns/service.py`):**
- `remaining_cycle_tokens()` (service): returns `None` without a budget.
- Token-exhaustion guard: `remaining is not None and remaining <= 0` (was unconditional).

**Lifecycle (`campaigns/lifecycle.py`):**
- Two token-limit checks (`usable_tokens(c.budget) - tokens_used <= 0`) replaced with `cycle_tokens_spent(c.budget, tokens_used)`, which is never true without a budget.

**Service / execution limits (`campaigns/service.py`):**
- Cycle and retrospective invocations: skip injecting `max_total_tokens` when no budget. Retrospective bounded only by `RETRO_MAX_SECONDS`.

**Extend (`campaigns/service.py` — `extend_campaign`):**
- Without a budget, token-raise requests return `NO_TOKEN_BUDGET`.
- `budget_exhausted` reopen check: a missing budget counts as "budget clear".

**Edit / actions (`campaigns/actions.py`):**
- `budget.max_total_tokens: null` in a PATCH deletes the key.
- If the goal was `budget_exhausted` and the budget is removed, the same reopen block used by `extend_campaign` is executed within the same transaction.

**API (`api/v1/campaigns.py`):**
- `BudgetIn.max_total_tokens: int | None = None` (a given value is checked by the policy: positive integer, `INVALID_BUDGET`).
- `CampaignCreate.budget: BudgetIn = Field(default_factory=BudgetIn)`.
- `BudgetPatch` distinguishes explicit `null` (remove) from absent key (no change) via `model_fields_set`.
- Response model: `usable_tokens: int | None`.

**MCP tool (`mcp/draft_tools.py`):**
- `propose_goal`: `budget` removed from required fields; description says to leave it out unless the person asked for a token limit. The `/goal` draft brief (`workspace/drafts.py`) and the `create_goal` workspace action description (`workspace/policy.py`) say the same.

**Brief (`campaigns/brief.py`):**
- Budget-remaining line shown only when a budget exists.
- Retro stats: "Tokens used: N of M" → "Tokens used: N" without a budget.

**Views (`campaigns/views.py`):** `usable_tokens` serialised as nullable.

**Frontend (`frontend/src/`):**
- `lib/campaigns.ts`: `CampaignBudget.max_total_tokens?: number | null`, `Campaign.usable_tokens: number | null`.
- `campaignDraft.ts`: `budgetTokens` defaults to `''`; empty string sends `budget: {}`.
- `CampaignForm.tsx`: token budget input moved inside a collapsed `<details>` ("Advanced"); hint text updated.
- `DraftParts.tsx`: budget line hidden when absent.
- `CampaignSection.tsx` (right rail): shows "Tokens used N" without a budget; extend-budget input hidden without a budget.
- i18n catalogs updated (ko/en).

**Docs (`docs/requirements.md`):**
- CMP-01 reworded: token budget is optional; absent → no token-based termination.
- CMP-10 scoped: "Goals **with** a token budget…"

## Decisions

**Decision 1 — default termination without a budget**

| Option | Trade-off | Verdict |
|---|---|---|
| A. No upper bound — rely on plateau (5 cycles), failure (3 cycles), deadline | Unbounded cost if goal keeps making progress | **Chosen** |
| B. Hard cap at 50 cycles | Predictable, but stops a goal that is still improving | Rejected by user: no reason to stop while there is genuine progress |
| C. Require a deadline for budgetless goals | Adds no new concepts | Rejected: adds friction back to `/goal` input |

Rationale: plateau detection (5 cycles with no improvement) already catches spinning goals. Genuine progress with no token budget is the user's desired outcome, not a problem. Visibility for long-running goals is delegated to #964 (mid-run reporting at every 10 cycles).

**Decision 2 — representation of "no budget"**

| Option | Verdict |
|---|---|
| Absent `max_total_tokens` key; `usable_tokens()→None` | **Chosen** — zero migration; existing rows carry the key and are unaffected |
| Sentinel `max_total_tokens: 0` | Conflicts with existing code that reads 0 as "immediately exhausted" |
| Separate column `token_budget_enabled` | Requires migration; two fields can drift out of sync |

**Decision 3 — removing the budget on a `budget_exhausted` goal**

| Option | Verdict |
|---|---|
| Edit removes budget only; user must call extend to reopen | Extend API requires token or deadline raise — no path to reopen a budgetless goal → dead end |
| Remove budget and reopen in the same transaction via extend's reopen block | **Chosen** — reuses `stop_reason`/`retro_due`/`next_cycle_at` reset logic already tested |

Budget removal is a human action through `PATCH /api/v1/campaigns/{id}` only: there is no edit screen in the UI yet and no agent tool edits a goal's budget. Other edits on a `budget_exhausted` goal are still refused (409); if its deadline has also passed, removing the budget is refused with `EXTEND_DOES_NOT_REOPEN`.

**Assumption flagged for future revisit:** a single very expensive cycle is not capped. The existing idle timeout (#892) still applies. If extremely high per-cycle costs become a problem this decision should be revisited and a per-cycle token limit considered.

## Result

- Goals created without `budget.max_total_tokens` run indefinitely on tokens; they terminate only on plateau, evaluation failure, deadline, or human intervention.
- Budgeted goals behave exactly as before (no regression).
- The `budget_exhausted` state remains reachable only for goals that explicitly set a budget.
- `propose_goal` no longer inserts a small default budget that was routinely overrun.
- UI presents the token budget as an opt-in advanced feature, reducing onboarding friction.
- `usable_tokens: null` in API responses signals "no limit" to all consumers.
- CMP-01 and CMP-10 requirements updated to match the new invariants.
