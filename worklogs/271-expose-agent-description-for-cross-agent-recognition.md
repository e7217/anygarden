# feat(agents): expose agent description for cross-agent recognition (#271)

- Commit: `1166cf0` (1166cf02e5b00c38ba34c405596499e250022079)
- Author: Changyong Um
- Date: 2026-04-26T00:12:38+09:00
- PR: #271

## Situation

doorae's cross-agent recognition was structurally name-only. The `Agent` row had no public-facing description field; the WS `ParticipantBrief` exposed only `id`, `display_name`, `kind`, and `agent_id`; the orchestrator-side LLM roster builder (`_build_roster_suffix`) emitted `- <@user:{uuid}> {name} ({kind})` lines with no semantic signal beyond the name. As a result, an LLM deciding which peer to hand off to had to either rely on name conventions or guess, and the frontend's mention popover and participant list could not surface anything that distinguished two agents whose names happened to read similarly.

## Task

- Add a nullable, peer-visible `description` field on `Agent` without disturbing the agent's *self-directed* `agents_md` body.
- Carry the field through every existing read path: REST (`AgentCreate`/`AgentUpdate`/`AgentOut`), WS welcome roster (`ParticipantBrief`), agent runtime roster, frontend `Participant` shape.
- Cap the value at 200 characters end-to-end so the per-turn token cost on the LLM roster stays bounded.
- Treat description-only updates as peer metadata: do not respawn the agent (its own subprocess never reads this field).
- Provide editing (AgentSettingsDialog) and display (MentionPopover, ParticipantListPopover) UI in the same change so the feature is usable, not just provisioned.
- Preserve backwards compatibility: pre-#271 agents and pre-#271 servers must continue to render the original "name only" line.

## Action

- **DB schema (`packages/cluster/doorae/db/models.py:295`)** — added `description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)` to `Agent`. Alembic revision `033_agent_description.py` (`packages/cluster/doorae/db/migrations/versions/033_agent_description.py`) modelled on `030_agent_memory_md.py`: `batch_alter_table("agents") → add_column("description", Text, nullable=True)` with a symmetric `drop_column` downgrade.
- **REST (`packages/cluster/doorae/api/v1/agents.py`)** — `AgentCreate`/`AgentUpdate`/`AgentOut` gained `description`. `AgentCreate` and `AgentUpdate` pin `Field(default=None, max_length=200)`; `AgentUpdate` follows the established `_set` flag idiom (`description_set: bool = False`) so an unrelated PATCH cannot wipe the field.
- **PUT semantics renaming** — the `non_avatar_changed` / `avatar_changed` counters in `update_agent` were renamed to `runtime_changed` / `peer_metadata_changed` so the description (and existing avatar fields) live under a name that explains *why* they skip `bump_generation`: peers, not the agent's own subprocess, are the consumers.
- **WS (`packages/cluster/doorae/ws/protocol.py:182-201`, `doorae/ws/handler.py:296-308`)** — `ParticipantBrief.description: Optional[str] = None`. `_build_participants_brief` reads `p.agent.description` on the agent branch; user/guest entries leave it `None`.
- **Agent runtime (`packages/agent/doorae_agent/integrations/claude_code.py:299-340`)** — `_build_roster_suffix` now appends ` — {description}` when the brief carries one, normalizing CR/LF to spaces and double-capping at 200 chars (defense in depth on top of the REST cap). Empty descriptions fall through to the legacy line.
- **Frontend types (`packages/cluster/frontend/src/hooks/useAgents.ts`, `src/pages/ChatPage.tsx`, `src/components/MentionPopover.tsx`, `src/components/AgentSettingsDialog.tsx`, `src/components/agent-settings/OverviewPanel.tsx`)** — `Agent`, `Participant`, `MentionOption`, and the `updateAgent` PATCH signature all gained `description?: string | null`. `ChatPage.mentionUsers` and `GuestRoomPage.mentionParticipants` propagate it from the participant cache to the popover options.
- **Frontend input (`OverviewPanel.tsx:79-280`)** — added a 2-row `<textarea>` with `maxLength=200`, blur-commit semantics that mirror the existing name field, an inline `n/200` counter, an Escape-to-revert handler, and helper copy that calls out the field's audience. Empty input is committed as `null` so admins can clear an outdated introduction.
- **Frontend display** — `MentionPopover.tsx` now stacks the agent name and description in a column inside each option (single-line layout preserved when description is absent). `ParticipantListPopover.tsx` does the same in each row.
- **Tests** — extended `tests/test_models.py` (model nullability + roundtrip), `tests/test_agents_api.py` (POST description, 422 on >200 chars, PATCH set + clear + no-bump), `tests/test_ws_handler.py` (welcome includes agent description; user is `None`), `tests/test_integrations/test_claude_code.py` (peer with description renders em-dash, peer without keeps legacy line, 500-char input truncates to exactly 200), `OverviewPanel.test.tsx` (5 new cases covering blur-commit, clear, no-op, counter, Escape revert). Bumped the alembic head pin in `tests/test_migrations.py` from "032" to "033".

## Decisions

The full design discussion lives in `docs/plans/2026-04-25-agent-description-design.md` and the implementation plan in `.tmp/plan-271-agent-description.md`. The load-bearing choices:

- **New `description` column over reusing `agents_md`** — `agents_md` is the agent's *self-directed* prompt body; mixing it with an *outward* introduction would force one of the two readers to live with the wrong tone and length. A separate column also keeps schema migrations isolated as the field's meaning evolves. The cost (one alembic revision) was outweighed by the precedent: `030_agent_memory_md.py` is a proven template for nullable Text additions.
- **Nullable, not required** — picking `NOT NULL` with a default empty string would have meant a forced backfill choice across every existing agent and a `strip() == ""` check that adds no value over the `is None` check. Nullable keeps adoption gradual; consumers fall back to the legacy line when it's `None`.
- **Inline LLM roster, not on-demand `lookup_agent` tool** — chat is interactive and round-trips are expensive; an LLM that doesn't *know* it should look up peers won't. With typical room sizes in the single-to-low-double digits and the 200-char cap, the per-turn cost stays around 1.5 K tokens, which is well within budget. The decision deliberately leaves the door open for a hybrid (rich peer metadata via tool, short tagline inline) once description proves insufficient on its own.
- **200-char application cap on a `Text` DB column** — keep schema flexible (no future migration if the cap is loosened), enforce the bound where it matters (REST and runtime). The runtime side double-caps because the cache pulls dicts directly from the welcome frame and a buggy/older server could send a longer value.
- **Description-only PATCH skips `bump_generation`** — the agent's own subprocess never reads this column; only peers do, on their next welcome. Restarting the subprocess for a description swap would be observable churn for zero correctness benefit. The `non_avatar_changed`/`avatar_changed` flags were already encoding this distinction; they were renamed to `runtime_changed`/`peer_metadata_changed` so the rule survives the next reader.
- **Backend + input + display in one PR** — provisioning the column without an editing surface would have left the field permanently empty for everyone except a hand-rolled SQL session, and shipping editing without display would have hidden the value the field exists to provide. The three changes share the same `ParticipantBrief` plumbing, so co-shipping costs little extra surface area.

Open assumptions — if violated, revisit:
- Single-room participant counts stay within a few dozen; otherwise the inline roster's per-turn token cost grows linearly and the hybrid path becomes attractive.
- agent-ts does not yet inject an LLM roster of its own. When it does, the same `description` plumbing should be ported over (`packages/agent-ts/src/coordination/room-query.ts:39-183` already passes through `display_name` and is the obvious extension point).

## Result

- 763 cluster tests + 288 agent tests + 332 frontend tests all pass with the change applied. The agent suite needs `OPENAI_API_KEY` to be set (preexisting requirement, unrelated to this PR).
- `make lint` reports the same 128 preexisting errors as `main`; this change introduces zero new lint findings.
- Alembic head moves from `032` → `033`; `alembic upgrade head` and `downgrade -1` both succeed in `tests/test_migrations.py`.
- Admins can now set, edit, and clear an agent's description from the Overview panel; the description appears as a secondary line in the mention autocomplete and participant popover; peer-orchestrator LLMs receive the same description appended to each non-self roster line.
- Pending follow-ups: agent-ts does not consume the field yet; `python-multipart` was found missing from the cluster package's optional `dev` extras (it was preinstalled in the existing main worktree). Both are tracked separately from this PR's scope.
