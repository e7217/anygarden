import { useState } from 'react'
import { Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'

interface TurnCancelControlProps {
  /** Open turns this control cancels; renders nothing when empty. */
  requestIds: string[]
  /** Accessible name, e.g. "Stop Helper" or "Stop all work in this thread". */
  label: string
  /** Visible text beside the icon; icon-only when omitted. */
  text?: string
  onCancel: (requestIds: string[]) => Promise<boolean>
}

/** ENG-09 — stop button for one agent's turn, or for every turn in a thread.
 *
 *  Execution-bound turns never reach it: the open-turn listing excludes
 *  them server-side because they use the execution cancel path. */
export default function TurnCancelControl({ requestIds, label, text, onCancel }: TurnCancelControlProps) {
  const { t } = useLocale()
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  if (requestIds.length === 0) return null

  const handleCancel = async () => {
    setPending(true)
    setFailed(false)
    try {
      setFailed(!(await onCancel(requestIds)))
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="ghost"
        size={text ? 'sm' : 'icon'}
        aria-label={label}
        title={label}
        disabled={pending}
        onClick={handleCancel}
        className="gap-1.5 text-[var(--color-foreground-muted)] hover:text-[var(--color-danger)]"
      >
        <Square aria-hidden="true" />
        {text && <span className="text-caption">{text}</span>}
      </Button>
      {failed && (
        <span role="alert" className="text-caption text-[var(--color-danger)]">
          {t('chat.stopAgentTurnFailed')}
        </span>
      )}
    </div>
  )
}
