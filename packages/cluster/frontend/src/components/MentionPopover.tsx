import { useEffect, useRef } from 'react'
import { useLocale } from '@/i18n/LocaleProvider'

export interface MentionOption {
  id: string
  display: string
  kind: 'user' | 'agent' | 'room' | 'file'
  // Issue #271 — short self-introduction shown as a secondary line
  // in the autocomplete so a user picking among multiple agents can
  // tell *what* each one does. Optional; absent for rooms, users,
  // and agents whose admin hasn't set a description yet.
  description?: string | null
}

interface MentionPopoverProps {
  options: MentionOption[]
  position: { top: number; left: number }
  selectedIndex: number
  onSelect: (option: MentionOption) => void
  onClose: () => void
}

export default function MentionPopover({
  options, position, selectedIndex, onSelect, onClose,
}: MentionPopoverProps) {
  const { t } = useLocale()
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = listRef.current?.children[selectedIndex] as HTMLElement | undefined
    el?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedIndex])

  if (options.length === 0) return null

  return (
    <div
      ref={listRef}
      className="absolute z-50 max-h-48 w-56 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-sm"
      style={{ bottom: position.top, left: position.left }}
    >
      {options.map((option, i) => {
        // #271 — render description as a secondary line so the user
        // can pick the right agent at a glance. Falls back to the
        // single-line legacy layout when description is absent so
        // rooms / users / pre-#271 agents stay compact.
        const desc = option.description?.trim()
        const hasDesc = !!desc
        return (
          <button
            key={option.id}
            className={`flex min-h-11 w-full items-start gap-2 px-3 py-1.5 text-sm text-left transition-colors ${
              i === selectedIndex
                ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-text)]'
                : 'text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)]'
            }`}
            onMouseDown={(e) => { e.preventDefault(); onSelect(option) }}
          >
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-alt)] text-[10px]">
              {option.kind === 'room'
                ? '#'
                : option.kind === 'file'
                  ? '$'
                  : option.kind === 'agent'
                    ? '🤖'
                    : option.display[0]?.toUpperCase()}
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="flex items-center gap-2">
                <span className="truncate">{option.display}</span>
                {option.kind === 'agent' && (
                  <span className="ml-auto shrink-0 text-[10px] text-[var(--color-foreground-subtle)]">{t('chat.agentLabel')}</span>
                )}
                {option.kind === 'file' && (
                  <span className="ml-auto shrink-0 text-[10px] text-[var(--color-foreground-subtle)]">{t('chat.fileLabel')}</span>
                )}
              </span>
              {hasDesc && (
                <span
                  className="truncate text-[11px] text-[var(--color-foreground-subtle)]"
                  data-testid="mention-option-description"
                >
                  {desc}
                </span>
              )}
            </span>
          </button>
        )
      })}
    </div>
  )
}
