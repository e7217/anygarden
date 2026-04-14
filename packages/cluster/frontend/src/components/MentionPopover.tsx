import { useEffect, useRef } from 'react'

export interface MentionOption {
  id: string
  display: string
  kind: 'user' | 'agent' | 'room'
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
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = listRef.current?.children[selectedIndex] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  if (options.length === 0) return null

  return (
    <div
      ref={listRef}
      className="absolute z-50 max-h-48 w-56 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-sm"
      style={{ bottom: position.top, left: position.left }}
    >
      {options.map((option, i) => (
        <button
          key={option.id}
          className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm text-left transition-colors ${
            i === selectedIndex
              ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand)]'
              : 'text-[var(--color-foreground)] hover:bg-black/[0.03]'
          }`}
          onMouseDown={(e) => { e.preventDefault(); onSelect(option) }}
        >
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--color-surface-alt)] text-[10px]">
            {option.kind === 'room' ? '#' : option.kind === 'agent' ? '🤖' : option.display[0]?.toUpperCase()}
          </span>
          <span className="truncate">{option.display}</span>
          {option.kind === 'agent' && (
            <span className="ml-auto text-[10px] text-[var(--color-foreground-subtle)]">agent</span>
          )}
        </button>
      ))}
    </div>
  )
}
