import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Send } from 'lucide-react'
import MentionPopover, { type MentionOption } from '@/components/MentionPopover'
import { insertMentionToken, extractMentionsMetadata, resolveRoomMentionsInText } from '@/lib/mentions'

interface MessageInputProps {
  onSend: (content: string, metadata?: Record<string, unknown>) => void
  onTyping: (isTyping: boolean) => void
  disabled?: boolean
  mentionUsers?: MentionOption[]
  mentionRooms?: MentionOption[]
}

interface MentionState {
  type: '@' | '#'
  startIndex: number
  query: string
}

/** Maps display text (e.g. "@홍길동") to token (e.g. "<@user:abc123>") */
interface TrackedMention {
  displayText: string
  token: string
}

export default function MessageInput({
  onSend, onTyping, disabled,
  mentionUsers = [], mentionRooms = [],
}: MessageInputProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [mention, setMention] = useState<MentionState | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [popoverPos, setPopoverPos] = useState({ top: 0, left: 0 })
  const trackedMentions = useRef<TrackedMention[]>([])

  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const maxHeight = 5 * 24
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px'
  }, [])

  useEffect(() => { autoResize() }, [value, autoResize])

  const currentOptions = mention?.type === '@' ? mentionUsers : mentionRooms
  const filtered = useMemo(
    () => mention
      ? currentOptions.filter(o => o.display.toLowerCase().includes(mention.query.toLowerCase()))
      : [],
    [mention, currentOptions],
  )

  useEffect(() => { setSelectedIndex(0) }, [mention?.type, mention?.query])

  const closeMention = useCallback(() => { setMention(null) }, [])

  const selectMention = useCallback((option: MentionOption) => {
    if (!mention) return
    const prefix = mention.type === '@' ? '@' : '#'
    const displayText = `${prefix}${option.display}`
    const tokenType = mention.type === '@' ? 'user' : 'room'
    const token = insertMentionToken(tokenType, option.id)
    // Show readable name in textarea, track mapping for send-time conversion
    const before = value.slice(0, mention.startIndex)
    const after = value.slice(mention.startIndex + 1 + mention.query.length)
    const newValue = before + displayText + ' ' + after
    setValue(newValue)
    trackedMentions.current.push({ displayText, token })
    setMention(null)
    setTimeout(() => {
      const el = textareaRef.current
      if (el) {
        const cursorPos = before.length + displayText.length + 1
        el.setSelectionRange(cursorPos, cursorPos)
        el.focus()
      }
    }, 0)
  }, [mention, value])

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    // Replace display names with ID-based tokens before sending
    let content = trimmed
    for (const m of trackedMentions.current) {
      content = content.split(m.displayText).join(m.token)
    }
    // Resolve any remaining directly-typed `#RoomName` plaintext into room tokens.
    // Issue #53: previously only autocomplete selections were tokenized.
    content = resolveRoomMentionsInText(content, mentionRooms)
    const mentions = extractMentionsMetadata(content)
    const metadata = mentions.length > 0 ? { mentions } : undefined
    onSend(content, metadata)
    setValue('')
    trackedMentions.current = []
    setMention(null)
    onTyping(false)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (mention && filtered.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex(i => Math.min(i + 1, filtered.length - 1))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex(i => Math.max(i - 1, 0))
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        selectMention(filtered[selectedIndex])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        closeMention()
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const updateMentionPosition = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    setPopoverPos({ top: el.offsetHeight + 4, left: 0 })
  }, [])

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value
    setValue(newValue)
    // Prune tracked mentions whose display text was edited/deleted
    trackedMentions.current = trackedMentions.current.filter(
      m => newValue.includes(m.displayText),
    )
    onTyping(true)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    typingTimeoutRef.current = setTimeout(() => onTyping(false), 2000)

    const cursorPos = e.target.selectionStart
    const textUpToCursor = newValue.slice(0, cursorPos)

    const atMatch = textUpToCursor.match(/(?:^|\s)@([^\s]*)$/)
    const hashMatch = textUpToCursor.match(/(?:^|\s)#([^\s]*)$/)

    if (atMatch) {
      const query = atMatch[1]
      const startIndex = cursorPos - query.length - 1
      setMention({ type: '@', startIndex, query })
      updateMentionPosition()
    } else if (hashMatch) {
      const query = hashMatch[1]
      const startIndex = cursorPos - query.length - 1
      setMention({ type: '#', startIndex, query })
      updateMentionPosition()
    } else {
      setMention(null)
    }
  }

  return (
    <div className="border-t border-[var(--color-border)] bg-white px-4 py-3">
      <div className="relative mx-auto flex w-full max-w-3xl items-end gap-2">
        {mention && filtered.length > 0 && (
          <MentionPopover
            options={filtered}
            position={popoverPos}
            selectedIndex={selectedIndex}
            onSelect={selectMention}
            onClose={closeMention}
          />
        )}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={disabled ? 'Connecting...' : 'Type a message... (@ to mention, # for rooms)'}
          rows={1}
          className="flex-1 resize-none rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white px-3 py-2 text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]/35 focus-visible:border-[var(--color-brand-focus)] disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
        />
        <Button
          size="icon"
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          title="Send message"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
}
