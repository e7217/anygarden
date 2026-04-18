/**
 * AvatarPickerDialog — Issue #101.
 *
 * Lets admins override an agent's seed-driven initial avatar with
 * either an emoji character or a lucide icon name. A third tab
 * ("Reset") is how they go back to the initials fallback.
 *
 * Why a dedicated dialog instead of expanding AgentEditDialog:
 * AgentEditDialog already owns the per-agent *manifest* (system
 * prompt + on-disk file tree). Avatar is pure UI metadata with a
 * different persistence path (avatar-only edits skip
 * ``bump_generation``), and its picker grid has completely
 * different affordances from the manifest editor. Conflating the
 * two would mean admins scrolling past a file tree to pick an
 * emoji — the opposite of "glance, click, done".
 *
 * Save semantics:
 * - The dialog holds a draft ``(kind, value)`` pair in local state.
 * - Applying a tab pre-fills that pair; clicking Save ships the
 *   ``*_set`` flags so the server can distinguish null-clear from
 *   "no change".
 * - The PATCH handler skips ``bump_generation`` when the avatar
 *   fields are the only ones that changed (see
 *   ``packages/cluster/doorae/api/v1/agents.py``), so saving an
 *   emoji does NOT restart the agent.
 */
import { useEffect, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import {
  CURATED_EMOJIS,
  CURATED_LUCIDE_NAMES,
  lookupLucideIcon,
} from '@/lib/avatar-options'
import type { Agent } from '@/hooks/useAgents'
import { cn } from '@/lib/utils'

interface Props {
  agent: Agent | null
  open: boolean
  onOpenChange: (open: boolean) => void
  updateAgent: (
    id: string,
    patch: {
      avatar_kind?: string | null
      avatar_kind_set?: boolean
      avatar_value?: string | null
      avatar_value_set?: boolean
    },
  ) => Promise<Agent>
}

type DraftAvatar = {
  kind: AvatarKind | null
  value: string | null
}

function asDraft(agent: Agent | null): DraftAvatar {
  if (!agent) return { kind: null, value: null }
  const kind = agent.avatar_kind
  if (kind === 'emoji' || kind === 'lucide') {
    return { kind, value: agent.avatar_value ?? null }
  }
  return { kind: null, value: null }
}

// Selected-swatch styling is shared across both grids to keep the
// visual language identical whether the admin is scanning emoji or
// lucide glyphs.
const SWATCH_BASE =
  'flex items-center justify-center h-10 w-10 rounded-[var(--radius-xs)] border border-transparent hover:bg-[var(--color-surface-alt)] transition-colors'
const SWATCH_SELECTED =
  'bg-[var(--color-brand-tint-bg)] border-[color:color-mix(in_srgb,var(--color-brand)_30%,transparent)]'

export default function AvatarPickerDialog({
  agent,
  open,
  onOpenChange,
  updateAgent,
}: Props) {
  const [draft, setDraft] = useState<DraftAvatar>(() => asDraft(agent))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Re-seed the draft whenever the dialog opens for a (potentially
  // different) agent, so opening "Edit avatar" on agent B after
  // previously editing agent A shows B's current state, not A's.
  useEffect(() => {
    if (open) {
      setDraft(asDraft(agent))
      setError(null)
    }
  }, [open, agent])

  const hasChanges =
    draft.kind !== (asDraft(agent).kind) ||
    draft.value !== (asDraft(agent).value)

  const initialTab: 'emoji' | 'lucide' | 'reset' =
    draft.kind === 'lucide' ? 'lucide' : draft.kind === 'emoji' ? 'emoji' : 'emoji'

  const handleSave = async () => {
    if (!agent) return
    setSaving(true)
    setError(null)
    try {
      await updateAgent(agent.id, {
        avatar_kind_set: true,
        avatar_kind: draft.kind,
        avatar_value_set: true,
        avatar_value: draft.value,
      })
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit avatar</DialogTitle>
          <DialogDescription>
            Pick an emoji or icon, or reset to the seed-driven initial.
            Changing the avatar does not restart the agent.
          </DialogDescription>
        </DialogHeader>

        {/* Live preview — always reflects the current draft. */}
        <div className="flex items-center gap-3 py-2">
          <EntityAvatar
            id={agent?.id ?? 'preview'}
            name={agent?.name ?? '?'}
            kind="agent"
            engine={agent?.engine}
            size="lg"
            avatarKind={draft.kind}
            avatarValue={draft.value}
            data-testid="avatar-picker-preview"
          />
          <div className="text-sm text-[var(--color-foreground-muted)]">
            {agent?.name ?? ''}
          </div>
        </div>

        <Tabs defaultValue={initialTab} className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="emoji" data-testid="avatar-picker-tab-emoji">
              Emoji
            </TabsTrigger>
            <TabsTrigger value="lucide" data-testid="avatar-picker-tab-lucide">
              Icon
            </TabsTrigger>
            <TabsTrigger value="reset" data-testid="avatar-picker-tab-reset">
              Reset
            </TabsTrigger>
          </TabsList>

          <TabsContent value="emoji" className="pt-3">
            <div
              className="grid grid-cols-8 gap-1"
              role="radiogroup"
              aria-label="Pick an emoji"
            >
              {CURATED_EMOJIS.map(e => {
                const selected = draft.kind === 'emoji' && draft.value === e
                return (
                  <button
                    key={e}
                    type="button"
                    className={cn(SWATCH_BASE, selected && SWATCH_SELECTED)}
                    onClick={() => setDraft({ kind: 'emoji', value: e })}
                    aria-pressed={selected}
                    data-testid={`avatar-picker-emoji-${e}`}
                  >
                    <span className="text-xl leading-none" aria-hidden="true">
                      {e}
                    </span>
                  </button>
                )
              })}
            </div>
          </TabsContent>

          <TabsContent value="lucide" className="pt-3">
            <div
              className="grid grid-cols-8 gap-1"
              role="radiogroup"
              aria-label="Pick an icon"
            >
              {CURATED_LUCIDE_NAMES.map(name => {
                const Icon = lookupLucideIcon(name)
                if (!Icon) return null
                const selected = draft.kind === 'lucide' && draft.value === name
                return (
                  <button
                    key={name}
                    type="button"
                    className={cn(SWATCH_BASE, selected && SWATCH_SELECTED)}
                    onClick={() => setDraft({ kind: 'lucide', value: name })}
                    aria-pressed={selected}
                    title={name}
                    data-testid={`avatar-picker-lucide-${name}`}
                  >
                    <Icon className="h-5 w-5 text-[var(--color-foreground)]" />
                  </button>
                )
              })}
            </div>
          </TabsContent>

          <TabsContent value="reset" className="pt-3">
            <div className="flex flex-col items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3">
              <p className="text-sm text-[var(--color-foreground-muted)]">
                Removes the custom avatar and restores the seed-driven
                initial. The agent's tone (background color) stays the
                same.
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDraft({ kind: null, value: null })}
                data-testid="avatar-picker-reset"
              >
                Remove custom avatar
              </Button>
            </div>
          </TabsContent>
        </Tabs>

        {error ? (
          <div className="mt-2 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error}
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || saving || !agent}
            data-testid="avatar-picker-save"
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
