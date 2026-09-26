import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/api'
import { useAuth } from '@/hooks/useAuth'
import { useLocale } from '@/i18n/LocaleProvider'

interface Props {
  roomId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved?: () => void
}

// Strategy options exposed to the admin UI. Must stay in sync with
// the server-side validator in ``anygarden/rooms/router.py`` —
// bidding / llm_judge are deliberately absent (plan-159 §1).
const STRATEGY_OPTIONS = [
  {
    value: 'mentioned_only',
    labelKey: 'rooms.mentionedOnly',
    hintKey: 'rooms.mentionedOnlyHint',
  },
  {
    value: 'round_robin',
    labelKey: 'rooms.roundRobin',
    hintKey: 'rooms.roundRobinHint',
  },
  {
    value: 'orchestrator',
    labelKey: 'rooms.orchestrator',
    hintKey: 'rooms.orchestratorHint',
  },
] as const

interface ParticipantLite {
  id: string
  agent_id: string | null
  kind: string
  display_name: string
}

interface PerAgentStat {
  participant_id: string
  agent_name: string
  tokens: number
  messages: number
  last_active_at: string | null
}

/**
 * Per-agent token usage panel (#159 Phase D).
 *
 * Renders the 1h / 24h per-agent token breakdown sourced from
 * ``GET /api/v1/rooms/:id/token-stats`` (admin-only). Useful for
 * spotting orchestrator rooms where one worker is burning the
 * token budget disproportionately.
 *
 * Usage rows use semantic surfaces and text tokens so they remain readable
 * in both themes. Raw numbers stay neutral rather than using an action color.
 */
function PerAgentTokenPanel({
  stats,
}: {
  stats: { window_1h: PerAgentStat[]; window_24h: PerAgentStat[] }
}) {
  const { t, formatNumber } = useLocale()
  // Merge 1h/24h on participant_id so a single row shows both
  // windows side-by-side. 24h is the union basis so an agent active
  // 2h ago still renders with a blank 1h column.
  const rows = (() => {
    const map = new Map<
      string,
      { name: string; tokens1h: number; tokens24h: number; lastActive: string | null }
    >()
    for (const row of stats.window_24h) {
      map.set(row.participant_id, {
        name: row.agent_name || row.participant_id,
        tokens1h: 0,
        tokens24h: row.tokens,
        lastActive: row.last_active_at,
      })
    }
    for (const row of stats.window_1h) {
      const entry = map.get(row.participant_id)
      if (entry) entry.tokens1h = row.tokens
      else
        map.set(row.participant_id, {
          name: row.agent_name || row.participant_id,
          tokens1h: row.tokens,
          tokens24h: 0,
          lastActive: row.last_active_at,
        })
    }
    return Array.from(map.values()).sort((a, b) => b.tokens24h - a.tokens24h)
  })()

  return (
    <div
      className="space-y-2 border-t border-[var(--color-border)] pt-4"
      data-testid="room-edit-token-panel"
    >
      <div className="flex items-baseline justify-between">
        <Label>{t('rooms.tokenUsage')}</Label>
        <span className="text-caption text-[var(--color-foreground-muted)]">
          {t('rooms.tokenEstimate')}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="text-caption text-[var(--color-foreground-muted)]">
          {t('rooms.noRecentTokens')}
        </p>
      ) : (
        <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-border)]">
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-surface-alt)] text-caption text-[var(--color-foreground-muted)]">
              <tr>
                <th className="px-3 py-1.5 font-medium">{t('rooms.agent')}</th>
                <th className="px-3 py-1.5 text-right font-medium">{t('rooms.oneHour')}</th>
                <th className="px-3 py-1.5 text-right font-medium">{t('rooms.twentyFourHours')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, idx) => (
                <tr
                  key={r.name + idx}
                  className="border-t border-[var(--color-border)]"
                >
                  <td className="px-3 py-1.5 text-[var(--color-foreground)]">
                    {r.name}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-[var(--color-foreground)]">
                    {formatNumber(r.tokens1h)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-[var(--color-foreground)]">
                    {formatNumber(r.tokens24h)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default function RoomEditDialog({ roomId, open, onOpenChange, onSaved }: Props) {
  const { t } = useLocale()
  const { user } = useAuth()
  const isAdmin = Boolean(user?.is_admin)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  // #225 — default flipped to true so the initial render (before the
  // GET settles) matches the server-side default. Non-admins never
  // see the toggle, but the payload gate below guarantees they can't
  // reach the admin-only field even if the load() callback hasn't
  // populated state yet.
  const [contextWindowEnabled, setContextWindowEnabled] = useState(true)
  // #159 Phase C — admin-only dispatch-mode controls. Non-admin
  // users still see the rest of the dialog but these fields stay
  // read-only (and the PATCH payload omits them).
  const [speakerStrategy, setSpeakerStrategy] = useState<string>('mentioned_only')
  const [orchestratorAgentId, setOrchestratorAgentId] = useState<string | null>(null)
  const [agentParticipants, setAgentParticipants] = useState<ParticipantLite[]>([])
  // #159 Phase D — per-agent token stats (admin-only). ``null``
  // until loaded or when the endpoint declines the caller. Stored
  // separately from the base GET so the two requests can race
  // safely without blocking the rest of the dialog.
  const [tokenStats, setTokenStats] = useState<{
    window_1h: PerAgentStat[]
    window_24h: PerAgentStat[]
  } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // #221 — transient success flash shown between ``Save`` and the
  // dialog close. The server now broadcasts ``room_settings_changed``
  // on admin PATCH so already-connected agents refresh their cached
  // dispatch mode without a reconnect; the banner tells the admin
  // that a subsequent message in the room will actually use the new
  // strategy rather than the old one.
  const [successFlash, setSuccessFlash] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!roomId) return
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`)
      if (!resp.ok) return
      const data = await resp.json()
      setName(data.name ?? '')
      setDescription(data.description ?? '')
      setContextWindowEnabled(Boolean(data.context_window_enabled))
      setSpeakerStrategy(data.speaker_strategy ?? 'mentioned_only')
      setOrchestratorAgentId(data.orchestrator_agent_id ?? null)
      const parts = Array.isArray(data.participants) ? data.participants : []
      setAgentParticipants(
        parts
          .filter((p: ParticipantLite) => p.kind === 'agent' && p.agent_id)
          .map((p: ParticipantLite) => ({
            id: p.id,
            agent_id: p.agent_id,
            kind: p.kind,
            display_name: p.display_name || '',
          })),
      )
      setError(null)
    } catch { /* ignore */ }
  }, [roomId])

  // #159 Phase D — token stats endpoint is admin-only (server
  // enforces via ``get_admin_identity``). Fetching it as a
  // non-admin is a safe 403; we just swallow the result.
  const loadTokenStats = useCallback(async () => {
    if (!roomId || !isAdmin) return
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/token-stats`)
      if (!resp.ok) {
        setTokenStats(null)
        return
      }
      const data = await resp.json()
      setTokenStats({
        window_1h: data?.window_1h?.per_agent ?? [],
        window_24h: data?.window_24h?.per_agent ?? [],
      })
    } catch {
      setTokenStats(null)
    }
  }, [roomId, isAdmin])

  useEffect(() => {
    if (open) {
      void load()
      void loadTokenStats()
    }
  }, [open, load, loadTokenStats])

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload: Record<string, unknown> = {
        name: name.trim(),
        description: description.trim() || null,
      }
      // Only admins can send the dispatch-mode + context-window
      // fields (#159 Phase C, #225). The server rejects non-admin
      // payloads with 403, but gating at the client too keeps the
      // request body clean and avoids surfacing a 403 on a rename
      // that happens to include ``context_window_enabled`` from
      // local state.
      if (isAdmin) {
        payload.speaker_strategy = speakerStrategy
        payload.orchestrator_agent_id = orchestratorAgentId
        payload.context_window_enabled = contextWindowEnabled
      }
      const resp = await apiFetch(`/api/v1/rooms/${roomId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        throw new Error(body.detail || t('rooms.saveFailed'))
      }
      onSaved?.()
      setSuccessFlash(t('rooms.saved'))
      // Brief flash before the dialog auto-closes so the admin sees
      // confirmation without needing a dedicated toast library.
      window.setTimeout(() => {
        setSuccessFlash(null)
        onOpenChange(false)
      }, 1400)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  const strategyHint = useMemo(
    () => {
      const key = STRATEGY_OPTIONS.find(o => o.value === speakerStrategy)?.hintKey
      return key ? t(key) : ''
    },
    [speakerStrategy, t],
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md max-h-[min(90dvh,52rem)] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('rooms.edit')}</DialogTitle>
          <DialogDescription>{t('rooms.editDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-2">
            <Label htmlFor="room-edit-name">{t('rooms.name')}</Label>
            <Input
              id="room-edit-name"
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="room-edit-desc">{t('rooms.description')}</Label>
            <textarea
              id="room-edit-desc"
              className="flex w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-focus)] focus:ring-offset-1 resize-none"
              placeholder={t('rooms.descriptionPlaceholder')}
              rows={3}
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>
          {/* #159 Phase C + #225 — admin-only room controls. The
              context-window toggle leads the block because it's the
              simplest switch; the speaker-strategy picker follows.
              Both fields live on the admin surface: flipping either
              silently changes who replies / how many tokens burn for
              every turn. */}
          {isAdmin && (
            <div className="space-y-4 border-t border-[var(--color-border)] pt-4">
              {/* #148 + #225 — ambient context window toggle. Replaces
                  the machine-level ``ANYGARDEN_CONTEXT_WINDOW_ENABLED``
                  env knob with a per-room admin toggle. Default is
                  True (see migration 028); un-checking opts the room
                  out of ambient sharing to save tokens. */}
              <div className="space-y-1.5">
                <label
                  htmlFor="room-edit-context-window"
                  className="flex cursor-pointer items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-border)] px-3 py-2.5"
                >
                  <input
                    id="room-edit-context-window"
                    data-testid="room-edit-context-window-toggle"
                    type="checkbox"
                    checked={contextWindowEnabled}
                    onChange={e => setContextWindowEnabled(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span className="flex-1 space-y-0.5">
                    <span className="block text-sm font-medium text-[var(--color-foreground)]">
                      {t('rooms.context')}
                    </span>
                    <span className="block text-caption text-[var(--color-foreground-muted)]">
                      {t('rooms.contextDescription')}
                    </span>
                  </span>
                </label>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="room-edit-speaker-strategy">
                  {t('rooms.speakerStrategy')}
                </Label>
                <select
                  id="room-edit-speaker-strategy"
                  data-testid="room-edit-speaker-strategy"
                  value={speakerStrategy}
                  onChange={e => setSpeakerStrategy(e.target.value)}
                  className="flex h-10 w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-focus)] focus:ring-offset-1"
                >
                  {STRATEGY_OPTIONS.map(opt => (
                    <option key={opt.value} value={opt.value}>
                      {t(opt.labelKey)}
                    </option>
                  ))}
                </select>
                <p className="text-caption text-[var(--color-foreground-muted)]">
                  {strategyHint}
                </p>

                {speakerStrategy === 'orchestrator' && (
                  <div className="space-y-1.5 pt-2">
                    <Label htmlFor="room-edit-orchestrator">{t('rooms.orchestrator')}</Label>
                    <select
                      id="room-edit-orchestrator"
                      data-testid="room-edit-orchestrator"
                      value={orchestratorAgentId ?? ''}
                      onChange={e =>
                        setOrchestratorAgentId(e.target.value || null)
                      }
                      className="flex h-10 w-full rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] px-3 text-sm text-[var(--color-foreground)] focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-focus)] focus:ring-offset-1"
                    >
                      <option value="">{t('rooms.notSelected')}</option>
                      {agentParticipants.map(p => (
                        <option key={p.id} value={p.agent_id ?? ''}>
                          {p.display_name || p.agent_id}
                        </option>
                      ))}
                    </select>
                    {agentParticipants.length === 0 && (
                      <p className="text-caption text-[var(--color-foreground-muted)]">
                        {t('rooms.noOrchestratorAgents')}
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* #159 Phase D — per-agent token usage panel. Admin-only;
              the token-stats endpoint refuses other callers. The
              backend per_agent aggregation already exists (#157 Phase
              C), so this is purely a surface. */}
          {isAdmin && tokenStats && (
            <PerAgentTokenPanel stats={tokenStats} />
          )}
        </div>

        {error && (
          <div className="rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error}
          </div>
        )}

        {successFlash && (
          <div
            role="status"
            className="rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2 text-sm text-[var(--color-foreground)]"
          >
            {successFlash}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>{t('common.cancel')}</Button>
          <Button onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? t('common.loading') : t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
