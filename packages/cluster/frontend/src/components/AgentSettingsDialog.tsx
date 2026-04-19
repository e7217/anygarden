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
import { deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile, AttachedSkill, SkillPreview } from '@/hooks/useAgents'
import OverviewPanel from '@/components/agent-settings/OverviewPanel'
import ManifestPanel from '@/components/agent-settings/ManifestPanel'
import RoomsPanel from '@/components/agent-settings/RoomsPanel'
import ActivityPanel from '@/components/agent-settings/ActivityPanel'
import { ChevronRight } from 'lucide-react'
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
    },
  ) => Promise<Agent>
  upsertAgentFile: (id: string, path: string, content: string) => Promise<AgentFile>
  deleteAgentFile: (id: string, path: string) => Promise<void>
  fetchAttachedSkills?: (id: string) => Promise<AttachedSkill[]>
  fetchSkillPreview?: (skillId: string) => Promise<SkillPreview | null>
  /** Fired when Rooms panel mutates so the caller can refresh its
   *  own derived state (e.g. comma-joined room names in a machine
   *  detail view). */
  onRoomsChange?: () => void
}

// Small uppercase label used for every section heading. Kept as a
// constant so the collapsible `<summary>` and the plain `<h3>` look
// identical.
const SECTION_HEADING_CLASS =
  'text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-foreground-muted)]'

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
      className="space-y-3"
    >
      <h3 id={`agent-settings-heading-${id}`} className={SECTION_HEADING_CLASS}>
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * Same visual shape as `<Section>` but the body is collapsed behind a
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
      className="group"
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
  onRoomsChange,
}: Props) {
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
                  online={deriveAgentOnline(agent.actual_state)}
                  agentState={agent.actual_state}
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

        {/* Single scrollable body — sections stack top-to-bottom.
            DESIGN.md §6.1: "No hard section borders — separation comes
            from background color changes and spacing". We rely on
            space-y-6 (24px) for the seam, not a divider. */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="px-6 py-5 space-y-6">
            <Section id="overview" title="Overview">
              <OverviewPanel agent={agent} updateAgent={updateAgent} />
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

            {/* Activity is a lifecycle log — least-often consulted of
                the four sections. Collapsed by default keeps Manifest
                and Rooms closer to the top of the scroll. */}
            <CollapsibleSection id="activity" title="Activity">
              <ActivityPanel agentId={agent?.id ?? null} />
            </CollapsibleSection>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
