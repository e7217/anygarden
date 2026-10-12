import { useEffect, useState } from 'react'
import { Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'

interface OpenTurn {
  request_id: string
  participant_id: string | null
}

interface TurnCancelControlProps {
  roomId: string
  participantId: string
  agentName: string
}

/** ENG-09 — stop button beside an agent's "working" indicator.
 *
 *  Looks up the agent's open general-room turn (execution-bound turns are
 *  excluded server-side; they use the execution cancel path) and renders
 *  only while one exists. */
export default function TurnCancelControl({ roomId, participantId, agentName }: TurnCancelControlProps) {
  const { t } = useLocale()
  const [requestId, setRequestId] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    setRequestId(null)
    setFailed(false)
    if (!roomId) return
    apiFetch(`/api/v1/turns/open?room_id=${encodeURIComponent(roomId)}`, {})
      .then(resp => (resp.ok ? resp.json() : []))
      .then((turns: OpenTurn[]) => {
        if (!active || !Array.isArray(turns)) return
        const turn = turns.find(row => row.participant_id === participantId)
        setRequestId(turn?.request_id ?? null)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [roomId, participantId])

  if (!requestId) return null

  const handleCancel = async () => {
    setPending(true)
    setFailed(false)
    try {
      const resp = await apiFetch(`/api/v1/turns/${encodeURIComponent(requestId)}/cancel`, { method: 'POST' })
      if (resp.ok) {
        setRequestId(null)
      } else {
        setFailed(true)
      }
    } catch {
      setFailed(true)
    } finally {
      setPending(false)
    }
  }

  const label = t('chat.stopAgentTurn', { name: agentName })
  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={label}
        title={label}
        disabled={pending}
        onClick={handleCancel}
        className="text-[var(--color-foreground-muted)] hover:text-[var(--color-danger)]"
      >
        <Square aria-hidden="true" />
      </Button>
      {failed && (
        <span role="alert" className="text-caption text-[var(--color-danger)]">
          {t('chat.stopAgentTurnFailed')}
        </span>
      )}
    </div>
  )
}
