/**
 * AgentSettingsDialog — unified agent settings (#158, restructured #165).
 *
 * Renders all four sections (Overview / Manifest / Rooms / Activity)
 * stacked vertically inside a single scrollable dialog body. The
 * earlier left-rail nav was removed in #165: with only four
 * destinations, the nav hid three of them behind a click without
 * much payoff. Stacking the sections lets the admin scan the whole
 * agent at a glance and scroll to whichever section matters.
 *
 * Panel lifecycle: every panel is always mounted when the dialog is
 * open. Unsaved Manifest edits therefore survive scrolling to other
 * sections (the earlier conditional-render design discarded them).
 *
 * Save semantics are section-scoped: Overview auto-saves on blur
 * (name) and on pick (avatar), Rooms mutates on click, Activity is
 * read-only, Manifest keeps its own bulk Save button. The dialog
 * itself has no footer bar.
 *
 * Style: follows DESIGN.md (warm neutral palette, whisper borders,
 * single-accent brand color).
 */
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import PresenceDot from '@/components/PresenceDot'
import { agentStatusLabel, deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile, AttachedSkill, SkillPreview, EngineCatalog } from '@/hooks/useAgents'
import OverviewPanel from '@/components/agent-settings/OverviewPanel'
import ManifestPanel from '@/components/agent-settings/ManifestPanel'
import RoomsPanel from '@/components/agent-settings/RoomsPanel'
import ActivityPanel from '@/components/agent-settings/ActivityPanel'
import TasksPanel from '@/components/agent-settings/TasksPanel'
import GoalsPanel from '@/components/agent-settings/GoalsPanel'
import { ChevronRight, EyeOff, Trash2, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { ReactNode } from 'react'

interface Props {
  agent: Agent | null
  open: boolean
  onOpenChange: (open: boolean) => void
  fetchAgentFiles: (id: string) => Promise<AgentFile[]>
  updateAgent: (
    id: string,
    patch: {
      name?: string
      agents_md?: string | null
      agents_md_set?: boolean
      avatar_kind?: string | null
      avatar_kind_set?: boolean
      avatar_value?: string | null
      avatar_value_set?: boolean
      model?: string | null
      model_set?: boolean
      reasoning_effort?: string | null
      reasoning_effort_set?: boolean
      description?: string | null
      description_set?: boolean
    },
  ) => Promise<Agent>
  upsertAgentFile: (id: string, path: string, content: string) => Promise<AgentFile>
  deleteAgentFile: (id: string, path: string) => Promise<void>
  fetchAttachedSkills?: (id: string) => Promise<AttachedSkill[]>
  fetchSkillPreview?: (skillId: string) => Promise<SkillPreview | null>
  /** Issue #217 — lets the Overview panel populate Model / Reasoning
   *  dropdowns. Returns ``null`` for engines the catalog doesn't
   *  know about (e.g. ``echo`` dev-only); OverviewPanel hides the
   *  dropdowns in that case. */
  fetchEngineCatalog?: (engine: string) => Promise<EngineCatalog | null>
  /** Fired when Rooms panel mutates so the caller can refresh its
   *  own derived state (e.g. comma-joined room names in a machine
   *  detail view). */
  onRoomsChange?: () => void
  /** #435 — option parity with ``AgentSettingsMenu``. When supplied, a
   *  footer surfaces the same per-agent admin actions the row menu has,
   *  so the action set no longer differs by entry point. Each renders
   *  only when its handler is provided ("show-when-permitted"). */
  onDelete?: () => void
  /** Current value of the context-window opt-out flag. Paired with
   *  ``onToggleContextWindowOptOut``: the footer renders a check-mark
   *  toggle when both are provided. */
  contextWindowOptOut?: boolean
  onToggleContextWindowOptOut?: () => void | Promise<void>
}

// Shared heading label (11px uppercase muted). Same classes are
// reused for the collapsible `<summary>` so both section types look
// identical.
const SECTION_HEADING_CLASS =
  'text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-foreground-muted)]'

// Card chrome per DESIGN.md §4 "Cards & Containers": white surface,
// whisper-weight border, 12px radius, 4-layer soft shadow, 20px
// padding. The dialog body sits on warm-white
// (`--color-surface-alt`) so these white cards visibly lift.
const SECTION_CARD_CLASS =
  'bg-white rounded-[var(--radius-lg)] border border-[var(--color-border)] shadow-card p-5'

function Section({
  id,
  title,
  children,
}: {
  id: string
  title: string
  children: ReactNode
}) {
  return (
    <section
      aria-labelledby={`agent-settings-heading-${id}`}
      data-testid={`agent-settings-section-${id}`}
      className={`${SECTION_CARD_CLASS} space-y-3`}
    >
      <h3 id={`agent-settings-heading-${id}`} className={SECTION_HEADING_CLASS}>
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * Same card chrome as `<Section>` but the body is collapsed behind a
 * native `<details>` so low-frequency sections (e.g. Activity) don't
 * steal scroll real estate from Manifest/Rooms by default. A rotating
 * chevron signals the collapsible affordance.
 */
function CollapsibleSection({
  id,
  title,
  children,
  defaultOpen = false,
}: {
  id: string
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  return (
    <details
      data-testid={`agent-settings-section-${id}`}
      className={`${SECTION_CARD_CLASS} group`}
      open={defaultOpen}
    >
      <summary
        className={`${SECTION_HEADING_CLASS} flex items-center gap-1.5 cursor-pointer list-none select-none`}
        aria-labelledby={`agent-settings-heading-${id}`}
      >
        <ChevronRight
          className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90"
          aria-hidden="true"
        />
        <span id={`agent-settings-heading-${id}`}>{title}</span>
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  )
}

export default function AgentSettingsDialog({
  agent,
  open,
  onOpenChange,
  fetchAgentFiles,
  updateAgent,
  upsertAgentFile,
  deleteAgentFile,
  fetchAttachedSkills,
  fetchSkillPreview,
  fetchEngineCatalog,
  onRoomsChange,
  onDelete,
  contextWindowOptOut,
  onToggleContextWindowOptOut,
}: Props) {
  const machineOffline = agent?.machine_online === false
  const agentOnline = deriveAgentOnline(agent?.actual_state, { machineOffline })
  const displayState = agentStatusLabel(agent?.actual_state, { machineOffline })

  // Footer option parity with AgentSettingsMenu (#435): the toggle row
  // appears only when both the value and its handler are supplied.
  const showContextToggle =
    typeof contextWindowOptOut === 'boolean' &&
    typeof onToggleContextWindowOptOut === 'function'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-5 pb-3 border-b border-[var(--color-border)]">
          <DialogTitle className="flex items-center gap-2">
            <span>Agent settings</span>
            {agent ? (
              <span className="inline-flex items-center gap-1.5 text-sm font-normal text-[var(--color-foreground-muted)]">
                <span className="text-[var(--color-foreground-subtle)]">—</span>
                <PresenceDot
                  variant="agent"
                  online={agentOnline}
                  agentState={displayState}
                />
                <span className="truncate max-w-[20rem]">
                  {agent.name}
                </span>
                <span className="text-[var(--color-foreground-subtle)]">
                  ({agent.engine})
                </span>
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription className="sr-only">
            View and edit agent identity, manifest, rooms, and activity.
          </DialogDescription>
        </DialogHeader>

        {/* Single scrollable body — each section is a standalone
            card (DESIGN.md §4) floating on a warm-white body
            (DESIGN.md §5.3 "Warm alternation"). The color step
            between white cards and the `#f6f5f4` body does the heavy
            lifting for section separation; the 1px whisper seam tried
            in #170 was too subtle on its own. */}
        <div className="flex-1 min-h-0 overflow-y-auto bg-[var(--color-surface-alt)]">
          <div className="px-6 py-5 space-y-3">
            <Section id="overview" title="Overview">
              <OverviewPanel
                agent={agent}
                updateAgent={updateAgent}
                fetchEngineCatalog={fetchEngineCatalog}
              />
            </Section>

            <Section id="manifest" title="Manifest">
              <ManifestPanel
                agent={agent}
                fetchAgentFiles={fetchAgentFiles}
                updateAgent={updateAgent}
                upsertAgentFile={upsertAgentFile}
                deleteAgentFile={deleteAgentFile}
                fetchAttachedSkills={fetchAttachedSkills}
                fetchSkillPreview={fetchSkillPreview}
                onNavigateAway={() => onOpenChange(false)}
              />
            </Section>

            <Section id="rooms" title="Rooms">
              <RoomsPanel agentId={agent?.id ?? null} onChange={onRoomsChange} />
            </Section>

            {/* Goals (#302) — recurring responsibilities the agent
                owns. Above Tasks because "what is this agent committed
                to over time" is a higher-level question than "what's
                open right now". */}
            <Section id="goals" title="Responsibilities">
              <GoalsPanel
                agentId={agent?.id ?? null}
                agentName={agent?.name ?? ''}
              />
            </Section>

            {/* Tasks (#266) — cross-room aggregation of work currently
                assigned to this agent. Sits next to Rooms because both
                answer "what is this agent doing right now". */}
            <Section id="tasks" title="Tasks">
              <TasksPanel agentId={agent?.id ?? null} />
            </Section>

            {/* Activity is a lifecycle log — least-often consulted of
                the four sections. Collapsed by default keeps Manifest
                and Rooms closer to the top of the scroll. */}
            <CollapsibleSection id="activity" title="Activity">
              <ActivityPanel agentId={agent?.id ?? null} />
            </CollapsibleSection>
          </div>
        </div>

        {/* Footer (#435) — per-agent admin actions, at parity with the
            row menu so the action set no longer depends on entry point.
            Renders only when at least one handler is supplied. */}
        {(showContextToggle || onDelete) && (
          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-[var(--color-border)] bg-white px-6 py-3">
            {showContextToggle ? (
              <button
                type="button"
                role="switch"
                aria-checked={contextWindowOptOut}
                onClick={() => void onToggleContextWindowOptOut!()}
                data-testid="agent-settings-context-window-opt-out"
                className="inline-flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 text-sm text-[var(--color-foreground)] hover:bg-black/5 cursor-pointer"
              >
                <EyeOff className="h-4 w-4" />
                <span>대화 맥락 공유 제외</span>
                {contextWindowOptOut ? (
                  <Check className="h-4 w-4 text-[var(--color-brand)]" aria-hidden="true" />
                ) : null}
              </button>
            ) : (
              <span aria-hidden="true" />
            )}
            {onDelete ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDelete()}
                data-testid="agent-settings-delete"
                className="text-[var(--color-destructive)] hover:bg-[var(--color-destructive)]/10"
              >
                <Trash2 className="h-4 w-4" />
                Delete agent
              </Button>
            ) : null}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
