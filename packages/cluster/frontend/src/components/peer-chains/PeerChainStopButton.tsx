import { useState } from 'react'
import { Square } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import { useLocale } from '@/i18n/LocaleProvider'
import type { PeerChain } from '@/lib/peerChains'

interface PeerChainStopButtonProps {
  chain: PeerChain
  onCancel: (chainId: string) => Promise<boolean>
  /** #1013 — the thread reply row's single [Stop] also stops the thread's
   *  other work, so it names and explains itself differently. */
  text?: string
  label?: string
  description?: string
}

/** #1006 — [Stop all]: confirms, then stops every open turn of the chain. */
export default function PeerChainStopButton({ chain, onCancel, text, label, description }: PeerChainStopButtonProps) {
  const { t } = useLocale()
  const { confirm } = useFeedback()
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)

  const handleStop = async () => {
    const ok = await confirm({
      title: t('peerChains.stopTitle'),
      description: description ?? t('peerChains.stopDescription', { name: chain.root.name }),
      confirmLabel: text ?? t('peerChains.stopAll'),
      destructive: true,
    })
    if (!ok) return
    setPending(true)
    setFailed(false)
    try {
      setFailed(!(await onCancel(chain.id)))
    } finally {
      setPending(false)
    }
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label={label ?? t('peerChains.stopAllLabel')}
        title={label ?? t('peerChains.stopAllLabel')}
        disabled={pending}
        onClick={handleStop}
        className="gap-1.5 text-[var(--color-foreground-muted)] hover:text-[var(--color-danger)]"
      >
        <Square aria-hidden="true" />
        <span className="text-caption">{text ?? t('peerChains.stopAll')}</span>
      </Button>
      {failed && (
        <span role="alert" className="text-caption text-[var(--color-danger)]">
          {t('peerChains.stopFailed')}
        </span>
      )}
    </span>
  )
}
