# refactor(agents): centralize memory/roster injection in base.py (#293)

- Commit: `2deb0b7` (2deb0b7c4e0455ed50e27b13ed6c32d0a5b9410b)
- Author: Changyong Um
- Date: 2026-04-28T01:10:51+09:00
- PR: #293

## Situation

#286 promoted `<room_conversation>` wrapping to `EngineAdapter.assemble_user_content`, but the rest of the per-turn context plumbing remained adapter-local. The same compose-and-concat pattern for the memory / shared-context block (#237 / #246 / #255) and the room roster (#221 / #279 / #288) was inlined in three places: `claude_code._build_options:262-308`, `codex.on_message:308-368`, and `gemini_cli._build_prompt:207-235`. Every new context feature cost three near-identical edits — and the inlined blocks had drifted: codex used a sha-tracked delta label for re-injection (because its session natively accumulates history), while claude_code (per-turn option rebuild) and gemini_cli (per-turn fresh subprocess) re-rendered every call without sha tracking. Codex also put roster *before* memory due to a quirk of its prepend pattern, while the other two emitted memory before roster. The resulting LOC and cognitive cost made the next context feature (a hypothetical fourth block) a three-PR exercise even though the logic was substantially the same.

## Task

- Add two helpers to `integrations/base.py`: a stateless `compose_session_context_suffix` for the assembly step, and a `ShaTrackedInjector` class for engines that need delta-labelled re-injection on history-accumulating sessions.
- Refactor all three CLI adapters to use the helpers, eliminating the inline memory/roster blocks while preserving the existing semantics for each adapter (system-prompt append for claude_code, sha-tracked turn prefix for codex, stateless preamble for gemini).
- Standardise the memory-then-roster order across all three adapters (codex's pre-#293 inverse order was an artifact of its prepend implementation, not an intentional design choice).
- Pin the helper contract with unit tests in `test_base_adapter.py` so the next context feature has a regression net at the level it actually lives.

## Action

- `packages/agent/doorae_agent/integrations/base.py` (+143 lines):
    - `compose_session_context_suffix(client, room_id, *, include_roster, with_collaborative_hint)` — calls the existing `compose_memory_suffix`, optionally appends the result of `client.compose_roster_suffix(...)` when the gate is on, and returns the joined suffix in `memory\n\nroster` order. Designed not to bake leading or trailing newlines so callers control attachment.
    - `class ShaTrackedInjector` — owns two `dict[str, str]` (memory_sha, roster_sha) keyed by `room_id`. `apply(...)` returns the prefix to prepend, with the memory block coming before the roster block when both are present. First emission per room is unlabelled; subsequent changes prepend the caller-supplied delta label. Stable inputs return `""`.
- `packages/agent/doorae_agent/integrations/claude_code.py:255-309` — replaced the four-step inline block (memory append, roster compute, roster gate, roster append) with one call into the helper. The orchestrator/collab gate is computed locally and passed as `include_roster=is_orchestrator or is_collab` with `with_collaborative_hint=is_collab`. System-prompt is still appended after `self._system_prompt` so AGENTS.md-derived personality drives behaviour.
- `packages/agent/doorae_agent/integrations/codex.py:189-211, 285-322` — `__init__` now creates a single `self._injector = ShaTrackedInjector()` instead of two `_memory_injected` / `_roster_injected` dicts. `on_message` computes both suffix bodies (memory unconditionally, roster only when `is_collaborative`) and delegates the prepend decision to `injector.apply(...)`, supplying the same `[공유 자료 업데이트]` / `[팀 구성 업데이트]` labels that #237 / #279 introduced.
- `packages/agent/doorae_agent/integrations/gemini_cli.py:198-227` — replaces the two inline blocks with one `compose_session_context_suffix(...)` call. Roster gate is `is_collab` (gemini does not host the orchestrator MCP wiring), and the result is appended to `parts` exactly as before. No injector — gemini spawns a fresh subprocess per turn, so re-injecting is cheap and stateless.
- `packages/agent/tests/test_integrations/test_base_adapter.py` (+232 lines):
    - `TestComposeSessionContextSuffix` — 7 cases: client `None`, no signals, memory-only with roster gate off, roster with collaborative hint, roster without hint (orchestrator path), memory-then-roster order, and no leading/trailing newline contract.
    - `TestShaTrackedInjector` — 5 cases: first turn (unlabelled), unchanged inputs (returns `""`), memory-only change (labelled re-inject), roster-only change (labelled re-inject), per-room isolation (sha keyed by `room_id`).

## Decisions

Sources mined: `.tmp/plan-293-context-injection-base-centralization.md` §3.2 capturing the alternatives, plus the pre-#293 inline blocks themselves which made the trade-offs concrete.

- **Function + class hybrid vs single class API**: chosen the hybrid. `claude_code` and `gemini_cli` *originally* don't need sha tracking — claude_code rebuilds `ClaudeAgentOptions` per turn (no native history accumulation between option rebuilds), gemini_cli spawns a fresh subprocess per turn (stateless by construction). Forcing a `ShaTrackedInjector` on them would create a "tracker" that resets every call, an empty abstraction. The plan §3.2 decision A captured this — the decisive observation was that sha tracking is *only* meaningful for engines whose session natively accumulates history (codex's `thread.run_text`).
- **Orchestrator gating in helper vs caller**: chosen caller. The helper takes `include_roster: bool` and `with_collaborative_hint: bool`; it does not know about orchestrator MCP wiring or per-engine gating policy. The decisive reason: orchestrator-MCP hosting is a per-adapter feature decision (only `claude_code` currently hosts `handoff_to`), and threading that into a generic context helper would couple the helper to a feature that adapters opt into independently. Plan §3.2 decision B.
- **Standardising memory-then-roster order**: chosen to standardise. Pre-#293 codex emitted roster-then-memory because each prepend put the new block at the front, so the *last* prepend (roster) ended up first. The plan §3.2 decision C originally claimed all three adapters were already on memory-then-roster, but reading the actual codex code revealed otherwise — codex was inverted. Re-deciding: standardise to memory-then-roster (the natural reading order: "here's the working set, then here's the team") rather than preserve codex's accidental order. Token content stays identical, only ordering of contextual blocks changes; LLM behaviour regression risk judged low because attention over a 2-block prefix is unlikely to be order-sensitive in this configuration. Documented as an explicit semantic shift in the commit body so future investigators of codex prompt drift have a pointer.
- **Single PR vs adapter-by-adapter**: chosen single PR. The helpers must land with at least one consumer to justify their existence (otherwise the PR is "add unused module"); landing all three at once gives the unit tests their full natural exercise surface and keeps the LOC delta self-contained. Plan §3.2 decision D.
- **Label strings stay caller-supplied**: chosen to leave `[공유 자료 업데이트]` and `[팀 구성 업데이트]` as parameters rather than bake them into the helper. The decisive reason was i18n: doorae is Korean-first today but the helper has no business making locale decisions, and a future locale-routing layer (per-adapter or per-room) would belong in the adapter, not the helper. Plan §3.2 decision E.

Assumptions to revisit if violated:

- Gemini CLI's stateless invocation model. If gemini ships a session API in a future release, gemini_cli would need a `ShaTrackedInjector` like codex; the helper's stateless function form would no longer fit. Re-evaluating that switch should be a separate PR.
- The standardised memory-then-roster order. If a future LLM behaviour audit shows codex specifically benefits from roster-first (perhaps because the roster's peer-mention hint primes the model for delegation more effectively when read first), reverting codex back to roster-then-memory is a one-line change in `ShaTrackedInjector.apply` (swap the order of the two `parts.append` blocks).
- The per-room sha cache lifetime. `ShaTrackedInjector` keeps shas for the lifetime of the adapter instance; codex's adapter instance lives as long as the agent process. If an agent ever needs to reset its memory of "what was injected" mid-process (e.g. on session timeout), it would need an explicit `injector.reset(room_id)` API which the helper does not currently expose.

## Result

- 308 agent-package tests pass (12 new tests under the two new `TestComposeSessionContextSuffix` and `TestShaTrackedInjector` classes), 814 cluster-package tests pass, 303 machine-package tests pass.
- `uv run ruff check packages/` reports 126 errors on the branch — identical to the post-#292 baseline. The single error visible in `gemini_cli.py:301` (`import os, signal` on one line) is pre-existing and not in any code touched by this PR.
- Net diff: 5 files changed, +447 insertions, -133 deletions. The growth is dominated by the unit tests (+232) and the helper docstrings (+143); the three adapter refactors are net negative (claude_code +25/-27, codex +20/-90, gemini_cli +14/-33).
- Codex's per-turn prompt order changes from roster→memory→user-content to memory→roster→user-content; documented in the commit body and this worklog as an intentional standardisation.
- The next context-block feature can land in `compose_session_context_suffix` (or a new sibling helper) and will automatically reach all three CLI adapters; a hypothetical "skill catalog" injection would be one helper edit plus three adapter no-touches instead of three identical adapter PRs.
- Pending: agent-ts (`packages/agent-ts/src/engines/`) does not yet have a TS-side equivalent of the helpers. Adding `compose_session_context_suffix` / `ShaTrackedInjector` to the TS package is the natural follow-up if the TS Claude Code adapter ever gains the `<shared-context>` / roster injection that the Python `claude_code.py` already has.
