import { useLocale } from '@/i18n/LocaleProvider'

interface RespondingIndicatorProps {
  className?: string
}

/**
 * MSG-09 — an agent is answering in this room (#912).
 *
 * Three dots rather than one so it never reads as the unread
 * ``UpdateDot`` or a presence dot, framed as a small typing bubble so it
 * never reads as the row's ``⋯`` menu either; neutral colour because
 * teal is kept for interaction intent. The dots step in sequence and hold still
 * under reduced motion (see ``.responding-dot`` in index.css).
 */
export default function RespondingIndicator({ className = '' }: RespondingIndicatorProps) {
  const { t } = useLocale()
  return (
    <span
      role="status"
      aria-label={t('common.responding')}
      title={t('common.responding')}
      className={`inline-flex h-3.5 shrink-0 items-center gap-0.5 rounded-full border border-[var(--color-border-strong)] px-1 text-[var(--color-foreground-muted)] ${className}`}
    >
      {[0, 1, 2].map(i => (
        <span
          key={i}
          aria-hidden="true"
          className="responding-dot h-[3px] w-[3px] rounded-full bg-current"
          style={{ animationDelay: `${i * 160}ms` }}
        />
      ))}
    </span>
  )
}
