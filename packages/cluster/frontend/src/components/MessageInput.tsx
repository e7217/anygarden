import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { ArrowUp, Paperclip, Plus, X } from 'lucide-react'
import MentionPopover, { type MentionOption } from '@/components/MentionPopover'
import { insertMentionToken, extractMentionsMetadata, resolveRoomMentionsInText } from '@/lib/mentions'
import { readDraft, writeDraft, clearDraft } from '@/lib/composerDrafts'
import { uploadRoomFile, type RoomSharedFile } from '@/lib/roomFiles'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import { parseSlashCommand } from '@/lib/slashCommands'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
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
  /** Overrides the composer prompt. The thread surfaces set this so a
   * nested composer says what it replies to — with two inputs on
   * screen, identical placeholders leave the target ambiguous. The
   * "Connecting..." disabled state still wins. */
  placeholder?: string
  /** Focus the textarea on mount. Set by the thread surfaces so opening
   * a thread puts the caret where the user is about to type. */
  autoFocus?: boolean
  /** Parks unsent text under this key so it survives unmount. The thread
   * layouts share one key per thread, which is what keeps a draft alive
   * across a panel/inline switch. Omitted = no draft retention. */
  draftKey?: string
  /** #739 — show a quiet reminder under the input while the draft calls
   * no one. Enabled for ``mentioned_only`` rooms with two or more agents,
   * where an unmentioned message gets no agent reply. */
  showMentionHint?: boolean
}

/** Id of the synthetic ``@everyone`` option; it inserts a literal keyword
 * rather than a ``<@user:id>`` token. */
const EVERYONE_OPTION_ID = '__everyone__'
/** Same boundaries as the server's ``parse_mentions`` (#739). */
const EVERYONE_PATTERN = /(?<!\w)@everyone(?![\w-])/
const USER_TOKEN_PATTERN = /<@user:[^>]+>/

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
  roomId, placeholder, autoFocus, draftKey, showMentionHint = false,
}: MessageInputProps) {
  const { t } = useLocale()
  const [value, setValue] = useState(() => readDraft(draftKey))
  // #269 — inline error from a malformed slash command (e.g. ``/task``
  // without an assignee, or a server-side 4xx). Cleared whenever the
  // user types again so a stale error doesn't linger after recovery.
  const [slashError, setSlashError] = useState<string | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Mirror the live value so an unmount (layout switch, thread close)
  // has nothing left to lose. Writing on change rather than on unmount
  // keeps this correct even when React discards the tree without
  // running cleanup in the order we'd expect.
  useEffect(() => {
    writeDraft(draftKey, value)
  }, [draftKey, value])

  // Restore when the key changes — switching threads swaps drafts
  // rather than carrying one thread's text into another.
  const lastDraftKey = useRef(draftKey)
  useEffect(() => {
    if (lastDraftKey.current === draftKey) return
    lastDraftKey.current = draftKey
    setValue(readDraft(draftKey))
  }, [draftKey])

  useEffect(() => {
    if (!autoFocus) return
    textareaRef.current?.focus()
  }, [autoFocus])
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The ``+`` button opens a small drop-up of composer actions; file
  // attachment is its only entry today.
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const addMenuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!addMenuOpen) return
    const onOutside = (event: PointerEvent) => {
      if (!addMenuRef.current?.contains(event.target as Node)) setAddMenuOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setAddMenuOpen(false)
      addMenuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    }
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [addMenuOpen])
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

  // #739 — ``@everyone`` leads the @ list whenever the room has an agent
  // to call; it is filtered by the query like any other option.
  const atOptions = useMemo<MentionOption[]>(
    () => mentionUsers.some(o => o.kind === 'agent')
      ? [
          {
            id: EVERYONE_OPTION_ID,
            display: 'everyone',
            kind: 'everyone',
            description: t('chat.everyoneDescription'),
          },
          ...mentionUsers,
        ]
      : mentionUsers,
    [mentionUsers, t],
  )

  const currentOptions = mention?.type === '@'
    ? atOptions
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
    // Show readable name in textarea, track mapping for send-time conversion
    const before = value.slice(0, mention.startIndex)
    const after = value.slice(mention.startIndex + 1 + mention.query.length)
    const newValue = before + displayText + ' ' + after
    setValue(newValue)
    // ``@everyone`` stays literal text; the server expands it (#739).
    if (option.kind !== 'everyone') {
      const tokenType = mention.type === '@' ? 'user' : 'room'
      const token = insertMentionToken(tokenType, option.id)
      trackedMentions.current.push({ displayText, token })
    }
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
                setSlashError(t('chat.taskCreateFailed', { status: r.status, detail }))
              } else {
                setSlashError(null)
              }
            })
            .catch(err => setSlashError(String(err)))
          // Clear the input regardless — the API call is in flight,
          // and a duplicate submission while waiting on the response
          // would just race with itself.
          setValue('')
          clearDraft(draftKey)
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
        : `📎 ${t('chat.filesCount', { count: attachments.length })}`
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
    clearDraft(draftKey)
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

  // #739 — the draft calls no one: no user token (picked or typed), no
  // ``@name`` of a room member, and no ``@everyone``. Slash commands are
  // not messages, so they never show the hint.
  const trimmedValue = value.trim()
  const draftCallsNoOne = showMentionHint
    && trimmedValue.length > 0
    && !trimmedValue.startsWith('/')
    && !EVERYONE_PATTERN.test(value)
    && !USER_TOKEN_PATTERN.test(value)
    && !trackedMentions.current.some(
      m => m.token.startsWith('<@user:') && value.includes(m.displayText),
    )
    && !mentionUsers.some(o => value.includes(`@${o.display}`))

  return (
    <div className="border-t border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
      <div className="relative mx-auto flex w-full max-w-3xl flex-col gap-2">
        {slashError && (
          <div
            data-testid="slash-error"
            className="text-xs text-[var(--color-destructive)]"
          >
            {slashError}
          </div>
        )}
        {(attachments.length > 0 || uploadError) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {attachments.map(a => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-0.5 text-xs text-[var(--color-foreground)]"
              >
                <Paperclip className="h-3 w-3 text-[var(--color-foreground-subtle)]" />
                <span className="max-w-[200px] truncate" title={a.filename}>
                  {a.filename}
                </span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a.id)}
                  className="flex h-8 w-8 items-center justify-center text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground)]"
                  aria-label={t('chat.removeAttachment', { name: a.filename })}
                  title={t('chat.remove')}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {uploadError && (
              <span className="text-xs text-[var(--color-destructive)]">{uploadError}</span>
            )}
          </div>
        )}
        <div className="relative flex w-full items-end gap-1 rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-1.5 transition-colors focus-within:border-[var(--color-brand-focus)] focus-within:ring-2 focus-within:ring-[var(--color-brand-focus)]/35">
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
            <div ref={addMenuRef} className="relative shrink-0">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={handleFileSelected}
                accept=".txt,.md,.markdown,.json,.yaml,.yml,.csv,.py,.html,.xml,text/*,application/json,application/yaml,application/xml"
              />
              <button
                type="button"
                onClick={() => setAddMenuOpen(open => !open)}
                disabled={disabled || uploading}
                title={t('chat.addMenu')}
                aria-label={t('chat.addMenu')}
                aria-haspopup="menu"
                aria-expanded={addMenuOpen}
                className="flex h-8 w-8 items-center justify-center rounded-full text-[var(--color-foreground-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="h-[18px] w-[18px]" />
              </button>
              {addMenuOpen && (
                <div
                  role="menu"
                  aria-label={t('chat.addMenu')}
                  className="absolute bottom-full left-0 z-50 mb-2 w-44 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] py-1 shadow-lg"
                >
                  <button
                    type="button"
                    role="menuitem"
                    autoFocus
                    onClick={() => {
                      setAddMenuOpen(false)
                      fileInputRef.current?.click()
                    }}
                    className="flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)]"
                  >
                    <Paperclip className="h-4 w-4 text-[var(--color-foreground-muted)]" />
                    {t('chat.attachFile')}
                  </button>
                </div>
              )}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={
              disabled
                ? t('chat.connecting')
                : placeholder ?? t('chat.messagePlaceholder')
            }
            rows={1}
            className="min-h-8 flex-1 resize-none bg-transparent px-2 py-1 text-sm leading-6 text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={
              disabled || uploading || (!value.trim() && attachments.length === 0)
            }
            title={t('chat.sendMessage')}
            aria-label={t('chat.sendMessage')}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--color-brand)] text-[var(--color-on-brand)] transition-colors hover:bg-[var(--color-brand-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-surface-hover)] disabled:text-[var(--color-foreground-subtle)]"
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        </div>
        {draftCallsNoOne && (
          <p
            data-testid="mention-hint"
            className="text-xs text-[var(--color-foreground-muted)]"
          >
            {t('chat.mentionHint')}
          </p>
        )}
      </div>
    </div>
  )
}
