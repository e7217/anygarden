import type { Participant } from '@/pages/ChatPage'
import type { AgentStage } from '@/lib/typingStage'
import { useLocale } from '@/i18n/LocaleProvider'

interface TypingIndicatorProps {
  typingUsers: Set<string>
  typingStages?: Record<string, AgentStage>
  participants: Record<string, Participant>
  myParticipantId: string | null
}

export default function TypingIndicator({
  typingUsers,
  typingStages = {},
  participants,
  myParticipantId,
}: TypingIndicatorProps) {
  const { t } = useLocale()
  // The slot stays mounted at a fixed height so that toggling the indicator
  // on/off never shifts the message list or reflows the composer. An empty
  // value shows as whitespace instead of collapsing the row.
  const others = Array.from(typingUsers).filter(pid => pid !== myParticipantId)

  const label = (() => {
    if (others.length === 0) return ''
    const names = others.map(pid => {
      const p = participants[pid]
      return p?.display_name ?? pid.slice(0, 8)
    })
    if (others.some(pid => typingStages[pid])) {
      return others.map((pid, index) => {
        const stage = typingStages[pid]
        if (!stage) return t('chat.isTyping', { name: names[index] })
        const stageKey = stage === 'preparing' ? 'chat.stagePreparing'
          : stage === 'using_tool' ? 'chat.stageUsingTool' : 'chat.stageWriting'
        return `${names[index]} · ${t(stageKey)}`
      }).join(', ')
    }
    if (names.length === 1) return t('chat.isTyping', { name: names[0] })
    if (names.length === 2) return t('chat.twoTyping', { first: names[0], second: names[1] })
    return t('chat.othersTyping', { first: names[0], second: names[1], count: names.length - 2 })
  })()

  return (
    <div
      className="mx-auto flex h-7 w-full max-w-3xl items-center border-t border-[var(--color-border)] bg-[var(--color-surface)] px-6"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="text-caption italic text-[var(--color-foreground-muted)]">
        {label || '\u00A0'}
      </span>
    </div>
  )
}
