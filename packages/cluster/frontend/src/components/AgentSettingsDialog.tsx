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
      <h3
        id={`agent-settings-heading-${id}`}
        className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-foreground-muted)]"
      >
        {title}
      </h3>
      {children}
    </section>
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

        {/* Single scrollable body — sections stack top-to-bottom. The
            ``divide-y`` separator between sections doubles as the
            visual seam so each heading doesn't need its own top
            border. */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <div className="px-6 py-5 divide-y divide-[var(--color-border)] [&>section]:py-5 first:[&>section]:pt-0 last:[&>section]:pb-0">
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

            <Section id="activity" title="Activity">
              <ActivityPanel agentId={agent?.id ?? null} />
            </Section>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
