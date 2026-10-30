# fix(workspace): let multi-approval requests continue and stop dropping a person's repeated instruction (#980)

- Commit: `a603fc6` (a603fc6d8e50f573a47ee792b5c4ca0b5d2c4df8)
- Author: Changyong Um
- Date: 2026-10-30T14:34:31+09:00
- PR: #980

## Situation

In the live "프로젝트관리" room a person asked the PM to create three per-project wiki agents (`hire_agent`, admin approval). The PM posted one approval card per turn because the tool told it to end its turn after any `pending_approval`, and the turn woken by each outcome only reported back, since the notice said "Do not repeat the request" and carried neither the original request nor the remaining steps. The person had to say "이어서 진행" at every step, and the second one was dropped as a cycle (`decide_policy.cycle_detected`) in 36 ms: the guard (#157) did not distinguish people from agents, and the 44-char `<@user:UUID>` token kept the short instruction above the 16-char exemption. Only two of the three agents were created.

## Task

- Allow `workspace_action` to return an existing pending card (`already_waiting: true`) across any turn, not only within the same turn's retries.
- Include the original person's request and any remaining pending cards in every approval outcome notice.
- Exempt human senders from the cycle-guard so repeated instructions are never dropped.
- Strip leading mention tokens before hashing so the routing prefix no longer crowds out body content in the 64-char hash window.
- Add requirements COL-11, WSP-09, WSP-10 and corresponding tests.

## Action

**`packages/cluster/anygarden/workspace/approvals.py`**
- Extracted `_waiting()` helper (`approvals.py:100–116`) that fetches an agent's non-expired pending cards for a room, optionally filtered by action.
- Changed `open_request()` (`approvals.py:68–98`) to match against `_waiting()` (room-scoped) instead of `turn_request_id`-scoped, so a duplicate proposal in any later turn returns the existing card.
- Added `_original_request()` (`approvals.py:353–393`) which follows the `workspace_action_result` metadata chain backwards up to `ORIGIN_HOPS=10` hops to find the person's originating message, neutralising `<@user:…>` and `@name` tokens before returning.
- Updated `_report()` (`approvals.py:325–350`) to embed the quoted original request and the list of remaining waiting cards in the outcome notice, and replaced "Do not repeat the request" with "If the original request still has steps left, continue them now."
- Added constants `ORIGIN_HOPS`, `ORIGIN_CHARS`, `WAITING_SHOWN`, `_ADDRESSES` at module level.

**`packages/cluster/anygarden/mcp/workspace_tools.py`**
- Updated the `workspace_action` tool description (`workspace_tools.py:47–57`) to instruct the agent to propose all required approvals in one turn.
- Added `already_waiting: True` to the `pending_approval` response payload (`workspace_tools.py:149`) when an existing card was returned, and updated `next` to reflect multi-approval flow.

**`packages/cluster/anygarden/agent/integrations/cycle_guard.py`**
- Added `_LEADING_MENTIONS` regex (`cycle_guard.py:33–38`) matching `<@user:UUID>` and `@everyone` prefixes.
- Modified `hash_content()` (`cycle_guard.py:45–55`) to strip leading mention tokens before taking the 64-char hash prefix.

**`packages/cluster/anygarden/agent/integrations/base.py`**
- Added `_sender_is_human()` helper (`base.py:704–720`) that returns `True` only when both the `_nonce` metadata field is absent and the room roster lists the sender as non-agent.
- Modified `decide_policy()` (`base.py:903–917`) to skip cycle detection entirely when `_sender_is_human()` is true.

**`docs/requirements.md`**
- Added COL-11 (human repeated instruction not dropped) and WSP-09/WSP-10 (multi-approval card dedup, outcome notice content).

**Tests** (`test_cycle_guard.py`, `test_should_respond.py`, `test_workspace_tools.py`)
- 24 new test lines covering hash stripping, human sender exemption, and multi-approval card dedup + outcome notice content.

## Decisions

**`already_waiting` scope: room+agent vs. turn**
- Option A (chosen): match on agent+room+action+params across all turns, return existing card with `already_waiting`.
- Option B: keep same-turn dedup and add a separate "continuation" path.
- Option A was chosen because the woken turn has a new `turn_request_id`, making turn-scoped dedup always miss. The room+agent scope correctly models "this card is still open."
- Assumption to revisit: if the same agent legitimately proposes the same action with identical params twice in a genuinely independent request, it will get the old card. This is unlikely in practice since params encode user-specific state, but worth monitoring.

**Original-request tracing: metadata chain vs. database join**
- Option A (chosen): follow `workspace_action_result.request_id` backward through `AgentTurn.trigger_message_id` up to 10 hops.
- Option B: store the root request ID at card creation and carry it forward.
- Option A was chosen to avoid a schema migration; the chain is shallow in practice (most requests have 1–2 hops). `ORIGIN_HOPS=10` bounds the walk.
- If the chain becomes deeper (multi-step workflows), `ORIGIN_HOPS` should be raised or Option B adopted.

**Cycle-guard human exemption: dual signal (nonce + roster)**
- Relying on only the `_nonce` field would exempt any message without a nonce, including unknown senders. Relying only on roster kind would fail when roster data is not loaded. The dual check (`_sender_is_human`) requires both signals to agree, keeping unknown senders guarded.

**Mention-stripping in `hash_content`: prefix only**
- Only leading tokens are stripped (not all mentions in the body) because the cycle guard's purpose is to detect near-identical *body* repeats. Interior mentions are part of the semantic content and should not be erased.

## Result

- A user request requiring N approvals can now post all N cards in one agent turn; subsequent woken turns get `already_waiting: True` instead of creating duplicate cards.
- Approval outcome notices carry a quoted copy of the person's original request and list the agent's remaining pending cards, enabling the agent to continue the work chain without re-reading history.
- Human senders are exempt from cycle detection; "계속해줘" repeated twice is now answered both times.
- The 64-char hash window now covers real message body content even when messages are prefixed with a 44-char routing token.
- Requirements COL-11, WSP-09, WSP-10 added; 265 lines of new code including tests.
- No schema migration required.
