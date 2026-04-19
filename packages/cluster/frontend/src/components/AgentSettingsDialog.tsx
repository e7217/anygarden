/**
 * AgentSettingsDialog — unified agent settings (#158).
 *
 * Collapses four previously-separate dialogs (Avatar / Manifest /
 * Rooms / Activity) into a single Dialog with a left-rail nav.
 *
 * Layout: a 200px vertical nav on the left, the active panel's body
 * on the right. max-w-5xl gives the manifest panel's 2-column
 * tree+editor grid room to breathe next to the nav.
 *
 * Panel lifecycle: sections are conditionally rendered, so each
 * panel mounts/unmounts with its selection. The trade-off is that
 * unsaved in-progress edits in Manifest are lost if the admin
 * switches away — acceptable today because the common path is
 * "open → edit manifest → save → close". If usage patterns change
 * we can hoist Manifest's working copy up to the dialog or keep
 * panels mounted via ``display: none``.
 *
 * Save semantics are section-scoped: Overview auto-saves on blur
 * (name) and on pick (avatar), Rooms mutates on click, Activity is
 * read-only, Manifest keeps its own bulk Save button. The dialog
 * itself has no footer bar.
 *
 * Style: follows DESIGN.md (warm neutral palette, whisper borders,
 * single-accent brand color for the active nav pill).
 */
import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { FileCog, History, DoorOpen, User } from 'lucide-react'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile, AttachedSkill, SkillPreview } from '@/hooks/useAgents'
import OverviewPanel from '@/components/agent-settings/OverviewPanel'
import ManifestPanel from '@/components/agent-settings/ManifestPanel'
import RoomsPanel from '@/components/agent-settings/RoomsPanel'
import ActivityPanel from '@/components/agent-settings/ActivityPanel'
import { cn } from '@/lib/utils'

export type SettingsSection = 'overview' | 'manifest' | 'rooms' | 'activity'

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

interface NavItem {
  id: SettingsSection
  label: string
  icon: typeof FileCog
}

const NAV_ITEMS: readonly NavItem[] = [
  { id: 'overview', label: 'Overview', icon: User },
  { id: 'manifest', label: 'Manifest', icon: FileCog },
  { id: 'rooms', label: 'Rooms', icon: DoorOpen },
  { id: 'activity', label: 'Activity', icon: History },
]

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
  const [section, setSection] = useState<SettingsSection>('overview')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-hidden flex flex-col p-0 gap-0">
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

        <div className="grid grid-cols-[200px_1fr] flex-1 min-h-0">
          {/* Left rail — section nav */}
          <nav
            className="border-r border-[var(--color-border)] bg-[var(--color-surface-alt)] py-3 overflow-y-auto"
            aria-label="Agent settings sections"
          >
            <ul className="space-y-0.5 px-2">
              {NAV_ITEMS.map(item => {
                const Icon = item.icon
                const isActive = section === item.id
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => setSection(item.id)}
                      aria-current={isActive ? 'page' : undefined}
                      data-testid={`agent-settings-nav-${item.id}`}
                      className={cn(
                        'w-full flex items-center gap-2 rounded-[var(--radius-md)] px-3 py-2 text-sm text-left transition-colors cursor-pointer',
                        isActive
                          ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] font-medium'
                          : 'text-[var(--color-foreground)] hover:bg-black/5',
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                      <span>{item.label}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </nav>

          {/* Right pane — section body */}
          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
            {section === 'overview' && (
              <OverviewPanel agent={agent} updateAgent={updateAgent} />
            )}
            {section === 'manifest' && (
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
            )}
            {section === 'rooms' && (
              <RoomsPanel agentId={agent?.id ?? null} onChange={onRoomsChange} />
            )}
            {section === 'activity' && (
              <ActivityPanel agentId={agent?.id ?? null} />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
