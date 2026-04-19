/**
 * OverviewPanel — agent identity and quick-glance metadata (#158).
 *
 * First section of the unified AgentSettingsDialog. Consolidates
 * information the admin previously had to hunt for across three
 * dialogs + one broken menu item:
 *
 * - Avatar — click to expand the emoji/icon picker inline.
 * - Name  — click-to-edit with blur-commit (persists via
 *   ``updateAgent({name})``).
 * - ID    — displayed as selectable text + Copy button with
 *   "Copied" feedback. Replaces the previous menu-level
 *   ``onCopyId`` handler, which silently swallowed failures in
 *   insecure contexts.
 * - Engine, State — read-only.
 *
 * The Copy ID button falls back gracefully when the clipboard API
 * rejects (insecure context, denied permissions): it selects the
 * ID text so the admin can copy it manually, and shows "Clipboard
 * unavailable" in place of "Copied".
 */
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Copy, Check, AlertCircle } from 'lucide-react'
import { EntityAvatar, type AvatarKind } from '@/components/EntityAvatar'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent } from '@/hooks/useAgents'
import AvatarPickerPanel from '@/components/agent-settings/AvatarPickerPanel'

type CopyState = 'idle' | 'ok' | 'fallback' | 'error'

// The `Agent.avatar_kind` column is typed as a loose `string` (it's
// open-ended at the DB layer), but EntityAvatar only understands
// `'emoji' | 'lucide'`. Unknown / missing values fall back to the
// seed-driven initial.
function narrowAvatarKind(
  raw: string | null | undefined,
): AvatarKind | null | undefined {
  if (raw === 'emoji' || raw === 'lucide') return raw
  return null
}

interface Props {
  agent: Agent | null
  updateAgent: (
    id: string,
    patch: {
      name?: string
      avatar_kind?: string | null
      avatar_kind_set?: boolean
      avatar_value?: string | null
      avatar_value_set?: boolean
    },
  ) => Promise<Agent>
}

export default function OverviewPanel({ agent, updateAgent }: Props) {
  const [showPicker, setShowPicker] = useState(false)
  const [nameDraft, setNameDraft] = useState(agent?.name ?? '')
  const [nameSaving, setNameSaving] = useState(false)
  const [nameError, setNameError] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<CopyState>('idle')
  const idRef = useRef<HTMLSpanElement>(null)

  // Re-seed the name draft whenever the target agent changes so the
  // input reflects the new agent's current name.
  useEffect(() => {
    setNameDraft(agent?.name ?? '')
    setNameError(null)
  }, [agent?.id, agent?.name])

  // Auto-clear copy feedback after 2 seconds. Depends on the
  // transitioning state so each new click resets the timer.
  useEffect(() => {
    if (copyState === 'idle') return
    const t = setTimeout(() => setCopyState('idle'), 2000)
    return () => clearTimeout(t)
  }, [copyState])

  if (!agent) {
    return (
      <div
        className="flex h-full items-center justify-center text-caption text-[var(--color-foreground-subtle)]"
        data-testid="overview-panel-empty"
      >
        No agent selected
      </div>
    )
  }

  const commitName = async () => {
    const trimmed = nameDraft.trim()
    if (trimmed === agent.name || trimmed === '') {
      setNameDraft(agent.name)
      setNameError(null)
      return
    }
    setNameSaving(true)
    setNameError(null)
    try {
      await updateAgent(agent.id, { name: trimmed })
    } catch (e) {
      setNameError(e instanceof Error ? e.message : String(e))
      setNameDraft(agent.name) // rollback on failure
    }
    setNameSaving(false)
  }

  const handleCopyId = async () => {
    try {
      await navigator.clipboard.writeText(agent.id)
      setCopyState('ok')
    } catch {
      // Fallback: select the ID span so the admin can ⌘/Ctrl+C.
      const el = idRef.current
      if (el && window.getSelection) {
        const sel = window.getSelection()
        const range = document.createRange()
        range.selectNodeContents(el)
        sel?.removeAllRanges()
        sel?.addRange(range)
      }
      setCopyState('fallback')
    }
  }

  return (
    <div className="space-y-5" data-testid="overview-panel">
      {/* Identity block — avatar + name */}
      <div className="flex items-start gap-4">
        <button
          type="button"
          onClick={() => setShowPicker(v => !v)}
          aria-expanded={showPicker}
          aria-label="Change avatar"
          data-testid="overview-avatar-trigger"
          className="rounded-full ring-offset-2 transition hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
        >
          <EntityAvatar
            id={agent.id}
            name={agent.name}
            kind="agent"
            engine={agent.engine}
            size="lg"
            avatarKind={narrowAvatarKind(agent.avatar_kind)}
            avatarValue={agent.avatar_value}
          />
        </button>

        <div className="flex-1 min-w-0 space-y-1">
          <Input
            value={nameDraft}
            onChange={e => setNameDraft(e.target.value)}
            onBlur={() => void commitName()}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault()
                ;(e.currentTarget as HTMLInputElement).blur()
              } else if (e.key === 'Escape') {
                setNameDraft(agent.name)
                ;(e.currentTarget as HTMLInputElement).blur()
              }
            }}
            disabled={nameSaving}
            aria-label="Agent name"
            data-testid="overview-name-input"
            className="text-base font-medium"
          />
          {nameError ? (
            <div
              className="flex items-center gap-1 text-xs text-[var(--color-warning)]"
              data-testid="overview-name-error"
            >
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
              {nameError}
            </div>
          ) : null}
        </div>
      </div>

      {/* Inline avatar picker — only visible when the admin clicks
          the avatar. AvatarPickerPanel fires onDone on Save or
          Cancel; we collapse the picker afterwards. */}
      {showPicker ? (
        <AvatarPickerPanel
          agent={agent}
          updateAgent={updateAgent}
          onDone={() => setShowPicker(false)}
        />
      ) : null}

      {/* Metadata grid */}
      <dl className="grid grid-cols-[6rem_1fr] gap-x-4 gap-y-3 text-sm">
        <dt className="text-[var(--color-foreground-muted)]">ID</dt>
        <dd className="flex items-center gap-2 min-w-0">
          <span
            ref={idRef}
            className="font-mono text-xs text-[var(--color-foreground)] bg-[var(--color-surface-alt)] rounded-[var(--radius-xs)] border border-[var(--color-border)] px-2 py-1 truncate"
            data-testid="overview-id-text"
          >
            {agent.id}
          </span>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => void handleCopyId()}
            title="Copy agent ID"
            data-testid="overview-copy-id"
          >
            {copyState === 'ok' ? (
              <Check className="h-4 w-4 text-[var(--color-success)]" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
          {copyState === 'ok' ? (
            <span
              className="text-xs text-[var(--color-success)]"
              data-testid="overview-copy-feedback"
            >
              Copied
            </span>
          ) : copyState === 'fallback' ? (
            <span
              className="text-xs text-[var(--color-foreground-muted)]"
              data-testid="overview-copy-feedback"
            >
              Clipboard unavailable — text selected
            </span>
          ) : null}
        </dd>

        <dt className="text-[var(--color-foreground-muted)]">Engine</dt>
        <dd className="text-[var(--color-foreground)]">{agent.engine}</dd>

        <dt className="text-[var(--color-foreground-muted)]">State</dt>
        <dd className="flex items-center gap-2">
          <PresenceDot
            variant="agent"
            online={deriveAgentOnline(agent.actual_state)}
            agentState={agent.actual_state}
          />
          <span className="text-[var(--color-foreground)]">
            {agent.actual_state}
          </span>
        </dd>
      </dl>
    </div>
  )
}
