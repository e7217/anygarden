import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Paperclip, Send, X } from 'lucide-react'
import MentionPopover, { type MentionOption } from '@/components/MentionPopover'
import { insertMentionToken, extractMentionsMetadata, resolveRoomMentionsInText } from '@/lib/mentions'
import { uploadRoomFile, type RoomSharedFile } from '@/lib/roomFiles'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import { parseSlashCommand } from '@/lib/slashCommands'
import { apiFetch } from '@/lib/api'
import {
  buildSharedFileReference,
  dedupeSharedFileReferences,
  resolveFileReferencesInText,
  type SharedFileReference,
} from '@/lib/fileReferences'

interface MessageInputProps {
  onSend: (content: string, metadata?: Record<string, unknown>) => void
  onTyping: (isTyping: boolean) => void
  disabled?: boolean
  mentionUsers?: MentionOption[]
  mentionRooms?: MentionOption[]
  /** Room the message will land in; required to upload file
   * attachments (#246). Falsy = upload UI is hidden. */
  roomId?: string
}

interface Attachment {
  id: string
  filename: string
  storage_name: string
  sha256?: string
}

interface MentionState {
  type: '@' | '#' | '$'
  startIndex: number
  query: string
}

/** Maps display text (e.g. "@홍길동") to token (e.g. "<@user:abc123>") */
interface TrackedMention {
  displayText: string
  token: string
}

interface TrackedFileReference {
  displayText: string
  reference: SharedFileReference
}

export default function MessageInput({
  onSend, onTyping, disabled,
  mentionUsers = [], mentionRooms = [],
  roomId,
}: MessageInputProps) {
  const [value, setValue] = useState('')
  // #269 — inline error from a malformed slash command (e.g. ``/task``
  // without an assignee, or a server-side 4xx). Cleared whenever the
  // user types again so a stale error doesn't linger after recovery.
  const [slashError, setSlashError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [mention, setMention] = useState<MentionState | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [popoverPos, setPopoverPos] = useState({ top: 0, left: 0 })
  const trackedMentions = useRef<TrackedMention[]>([])
  // Attachments uploaded since the last send; they're already stored
  // server-side at this point, we just carry their ids to include
  // in the next outbound message's ``references`` metadata.
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const { files: roomFiles, refresh: refreshRoomFiles } = useRoomFiles(roomId ?? null)

  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const maxHeight = 5 * 24
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px'
  }, [])

  useEffect(() => { autoResize() }, [value, autoResize])

  const fileOptions = useMemo<MentionOption[]>(
    () => roomFiles.map(file => ({
      id: file.id,
      display: file.filename,
      kind: 'file',
      description: file.storage_name === file.filename ? file.mime : file.storage_name,
    })),
    [roomFiles],
  )

  const currentOptions = mention?.type === '@'
    ? mentionUsers
    : mention?.type === '#'
      ? mentionRooms
      : fileOptions
  const filtered = useMemo(
    () => mention
      ? currentOptions.filter(o => o.display.toLowerCase().includes(mention.query.toLowerCase()))
      : [],
    [mention, currentOptions],
  )

  useEffect(() => { setSelectedIndex(0) }, [mention?.type, mention?.query])

  const closeMention = useCallback(() => { setMention(null) }, [])
  const trackedFileReferences = useRef<TrackedFileReference[]>([])

  const selectMention = useCallback((option: MentionOption) => {
    if (!mention) return
    if (mention.type === '$') {
      const file = roomFiles.find(f => f.id === option.id)
      if (!file) return
      const displayText = `$${file.filename}`
      const before = value.slice(0, mention.startIndex)
      const after = value.slice(mention.startIndex + 1 + mention.query.length)
      const newValue = before + displayText + ' ' + after
      setValue(newValue)
      trackedFileReferences.current.push({
        displayText,
        reference: buildSharedFileReference(file, 'inline'),
      })
      setMention(null)
      setTimeout(() => {
        const el = textareaRef.current
        if (el) {
          const cursorPos = before.length + displayText.length + 1
          el.setSelectionRange(cursorPos, cursorPos)
          el.focus()
        }
      }, 0)
      return
    }
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
  }, [mention, roomFiles, value])

  const handleSend = () => {
    const trimmed = value.trim()
    // A message is sendable if either the user typed something, or
    // they attached at least one file — "attach only" is a valid
    // action that announces the share in the room.
    if (!trimmed && attachments.length === 0) return
    if (disabled) return

    // Replace display names with ID-based tokens before sending
    let content = trimmed
    for (const m of trackedMentions.current) {
      content = content.split(m.displayText).join(m.token)
    }
    // Resolve any remaining directly-typed `#RoomName` plaintext into room tokens.
    // Issue #53: previously only autocomplete selections were tokenized.
    content = resolveRoomMentionsInText(content, mentionRooms)

    // #269 — Slash-command interception. Token resolution above must
    // run first so the parser sees ``<@user:pid>`` rather than the
    // legacy ``@DisplayName`` text. Unknown commands fall through to a
    // normal send (a user typing a URL like ``/path/to/x`` should not
    // be hijacked).
    if (content.startsWith('/') && roomId) {
      const dispatch = parseSlashCommand(content)
      if (dispatch) {
        const { command, parsed } = dispatch
        if (!parsed.ok) {
          setSlashError(parsed.error)
          return
        }
        if (command === 'task') {
          // Fire-and-forget; the WS task fanout will surface the new
          // row in TaskPanel and the synthetic mention card in chat.
          apiFetch(`/api/v1/rooms/${roomId}/tasks`, {
            method: 'POST',
            body: JSON.stringify({
              title: parsed.payload.title,
              assignee_participant_id: parsed.payload.assignee_pid,
            }),
          })
            .then(async r => {
              if (!r.ok) {
                const detail = await r.text().catch(() => '')
                setSlashError(`task 생성 실패 (${r.status}) ${detail}`)
              } else {
                setSlashError(null)
              }
            })
            .catch(err => setSlashError(String(err)))
          // Clear the input regardless — the API call is in flight,
          // and a duplicate submission while waiting on the response
          // would just race with itself.
          setValue('')
          trackedMentions.current = []
          trackedFileReferences.current = []
          setMention(null)
          onTyping(false)
          if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
          return
        }
      }
    }
    // Attach-only messages: render a short marker so the message has
    // visible content. The MessageBubble renderer may still choose
    // to present the attachment pill as the primary affordance.
    if (!content && attachments.length > 0) {
      content = attachments.length === 1
        ? `📎 ${attachments[0].filename}`
        : `📎 ${attachments.length} files`
    }
    const mentions = extractMentionsMetadata(content)
    const references = dedupeSharedFileReferences([
      ...trackedFileReferences.current.map(f => f.reference),
      ...resolveFileReferencesInText(content, roomFiles),
      ...attachments.map(a => ({
        type: 'shared_file' as const,
        id: a.id,
        name: a.filename,
        storage_name: a.storage_name,
        sha256: a.sha256,
        origin: 'attachment' as const,
      })),
    ])
    const metadata: Record<string, unknown> = {}
    if (mentions.length > 0) metadata.mentions = mentions
    if (references.length > 0) metadata.references = references
    onSend(content, Object.keys(metadata).length > 0 ? metadata : undefined)
    setValue('')
    trackedMentions.current = []
    trackedFileReferences.current = []
    setAttachments([])
    setUploadError(null)
    setMention(null)
    onTyping(false)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
  }

  const handleFileSelected = async (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = e.target.files?.[0]
    // Reset the input so selecting the same file twice in a row
    // still fires ``change``.
    e.target.value = ''
    if (!file || !roomId) return
    setUploading(true)
    setUploadError(null)
    try {
      const uploaded: RoomSharedFile = await uploadRoomFile(roomId, file)
      setAttachments(prev => [
        ...prev,
        {
          id: uploaded.id,
          filename: uploaded.filename,
          storage_name: uploaded.storage_name,
          sha256: uploaded.sha256,
        },
      ])
      void refreshRoomFiles()
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }

  const removeAttachment = (id: string) => {
    setAttachments(prev => prev.filter(a => a.id !== id))
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
    // #269 — clear stale slash error as soon as the user keeps typing
    // so a recovered input doesn't carry a red banner forever.
    if (slashError) setSlashError(null)
    // Prune tracked mentions whose display text was edited/deleted
    trackedMentions.current = trackedMentions.current.filter(
      m => newValue.includes(m.displayText),
    )
    trackedFileReferences.current = trackedFileReferences.current.filter(
      f => newValue.includes(f.displayText),
    )
    onTyping(true)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    typingTimeoutRef.current = setTimeout(() => onTyping(false), 2000)

    const cursorPos = e.target.selectionStart
    const textUpToCursor = newValue.slice(0, cursorPos)

    const atMatch = textUpToCursor.match(/(?:^|\s)@([^\s]*)$/)
    const hashMatch = textUpToCursor.match(/(?:^|\s)#([^\s]*)$/)
    const dollarMatch = textUpToCursor.match(/(?:^|\s)\$([^\s$()]*)$/)

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
    } else if (dollarMatch && roomFiles.length > 0) {
      const query = dollarMatch[1]
      const startIndex = cursorPos - query.length - 1
      setMention({ type: '$', startIndex, query })
      updateMentionPosition()
    } else {
      setMention(null)
    }
  }

  return (
    <div className="border-t border-[var(--color-border)] bg-white px-4 py-3">
      <div className="relative mx-auto flex w-full max-w-3xl flex-col gap-2">
        {slashError && (
          <div
            data-testid="slash-error"
            className="text-xs text-red-600"
          >
            {slashError}
          </div>
        )}
        {(attachments.length > 0 || uploadError) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {attachments.map(a => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-white px-2.5 py-0.5 text-xs text-[var(--color-foreground)]"
              >
                <Paperclip className="h-3 w-3 text-[var(--color-foreground-subtle)]" />
                <span className="max-w-[200px] truncate" title={a.filename}>
                  {a.filename}
                </span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  className="text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground)]"
                  aria-label={`Remove ${a.filename}`}
                  title="Remove"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {uploadError && (
              <span className="text-xs text-red-600">{uploadError}</span>
            )}
          </div>
        )}
        <div className="relative flex w-full items-end gap-2">
          {mention && filtered.length > 0 && (
            <MentionPopover
              options={filtered}
              position={popoverPos}
              selectedIndex={selectedIndex}
              onSelect={selectMention}
              onClose={closeMention}
            />
          )}
          {roomId && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={handleFileSelected}
                accept=".txt,.md,.markdown,.json,.yaml,.yml,.csv,.py,.html,.xml,text/*,application/json,application/yaml,application/xml"
              />
              <Button
                variant="outline"
                size="icon"
                onClick={() => fileInputRef.current?.click()}
                disabled={disabled || uploading}
                title="Attach file"
                aria-label="Attach file"
              >
                <Paperclip className="h-4 w-4" />
              </Button>
            </>
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
            disabled={
              disabled || uploading || (!value.trim() && attachments.length === 0)
            }
            title="Send message"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
