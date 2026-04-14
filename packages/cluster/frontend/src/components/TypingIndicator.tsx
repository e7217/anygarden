import type { Participant } from '@/pages/ChatPage'

interface TypingIndicatorProps {
  typingUsers: Set<string>
  participants: Record<string, Participant>
  myParticipantId: string | null
}

export default function TypingIndicator({
  typingUsers,
  participants,
  myParticipantId,
}: TypingIndicatorProps) {
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
    if (names.length === 1) return `${names[0]} is typing…`
    if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`
    return `${names[0]}, ${names[1]}, and ${names.length - 2} others are typing…`
  })()

  return (
    <div
      className="mx-auto flex h-7 w-full max-w-3xl items-center border-t border-[var(--color-border)] bg-white px-6"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="text-caption italic text-[var(--color-foreground-muted)]">
        {label || '\u00A0'}
      </span>
    </div>
  )
}
