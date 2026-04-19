/**
 * AvatarPickerPanel — extracted from AvatarPickerDialog (#158).
 *
 * Same picker UI (emoji / lucide / reset tabs, live preview) rendered
 * inline inside AgentSettingsDialog's Overview section. The outer
 * Dialog wrapper is gone; Save success or Cancel fires ``onDone`` so
 * the parent can collapse the inline picker.
 *
 * Save semantics match the previous dialog: the ``*_set`` flags ship
 * so the server distinguishes null-clear from no-change, and
 * avatar-only updates skip ``bump_generation`` (backend responsibility).
 */
import { useEffect, useState } from 'react'
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
  updateAgent: (
    id: string,
    patch: {
      avatar_kind?: string | null
      avatar_kind_set?: boolean
      avatar_value?: string | null
      avatar_value_set?: boolean
    },
  ) => Promise<Agent>
  /** Fired after Save succeeds or Cancel — the parent collapses the
   *  inline picker. */
  onDone?: () => void
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

const SWATCH_BASE =
  'flex items-center justify-center h-10 w-10 rounded-[var(--radius-xs)] border border-transparent hover:bg-[var(--color-surface-alt)] transition-colors'
const SWATCH_SELECTED =
  'bg-[var(--color-brand-tint-bg)] border-[color:color-mix(in_srgb,var(--color-brand)_30%,transparent)]'

export default function AvatarPickerPanel({ agent, updateAgent, onDone }: Props) {
  const [draft, setDraft] = useState<DraftAvatar>(() => asDraft(agent))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Re-seed the draft when the target agent changes so switching
  // agents from the parent shows the new agent's current state.
  useEffect(() => {
    setDraft(asDraft(agent))
    setError(null)
  }, [agent])

  const hasChanges =
    draft.kind !== asDraft(agent).kind || draft.value !== asDraft(agent).value

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
      onDone?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  return (
    <div className="space-y-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-3">
      <div className="flex items-center gap-3">
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
          <div className="flex flex-col items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-background)] p-3">
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
        <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
          {error}
        </div>
      ) : null}

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onDone?.()}
          disabled={saving}
        >
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={!hasChanges || saving || !agent}
          data-testid="avatar-picker-save"
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
