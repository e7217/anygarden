import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { ArrowUp, Paperclip, Plus, X } from 'lucide-react'
import MentionPopover, { type MentionOption } from '@/components/MentionPopover'
import { insertMentionToken, extractMentionsMetadata, resolveRoomMentionsInText } from '@/lib/mentions'
import { readDraft, writeDraft, clearDraft } from '@/lib/composerDrafts'
import { RoomFileUploadError, uploadRoomFile, type RoomSharedFile } from '@/lib/roomFiles'
import { uploadRoomArtifact } from '@/lib/roomArtifacts'
import { useRoomFiles } from '@/hooks/useRoomFiles'
import { parseSlashCommand } from '@/lib/slashCommands'
import { apiFetch } from '@/lib/api'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
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
   * no one. Enabled for rooms with two or more agents, where an
   * unmentioned message gets no agent reply. */
  showMentionHint?: boolean
}

/** Id of the synthetic ``@everyone`` option; it inserts a literal keyword
 * rather than a ``<@user:id>`` token. */
const EVERYONE_OPTION_ID = '__everyone__'
/** Same boundaries as the server's ``parse_mentions`` (#739). */
const EVERYONE_PATTERN = /(?<!\w)@everyone(?![\w-])/
const USER_TOKEN_PATTERN = /<@user:[^>]+>/

/** A file uploaded since the last send. Text goes to the room's shared
 * files (#246); images and PDFs are room artifacts (#873), shown with a
 * local thumbnail until the message goes out. */
type Attachment =
  | { kind: 'shared_file'; id: string; filename: string; storage_name: string; sha256?: string }
  | { kind: 'artifact'; id: string; filename: string; previewUrl?: string }

/** #873 — binaries that travel as room artifacts. SVG is left out on
 * purpose: a person's SVG can carry script once downloaded and opened. */
const ARTIFACT_TYPES: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  pdf: 'application/pdf',
}
const ARTIFACT_MIMES = new Set(Object.values(ARTIFACT_TYPES))
const TEXT_ACCEPT = '.txt,.md,.markdown,.json,.yaml,.yml,.csv,.py,.html,.xml,text/*,application/json,application/yaml,application/xml'
const FILE_ACCEPT = [
  TEXT_ACCEPT,
  ...Object.keys(ARTIFACT_TYPES).map(ext => `.${ext}`),
  ...ARTIFACT_MIMES,
].join(',')

/** Where a file goes: ``artifact``, ``shared_file``, or ``null`` when no
 * store takes it. Browsers sometimes leave ``type`` empty, so the
 * extension decides then. */
function attachmentStore(file: File): 'artifact' | 'shared_file' | null {
  const ext = /\.(\w+)$/.exec(file.name)?.[1].toLowerCase() ?? ''
  const mime = file.type || ARTIFACT_TYPES[ext] || ''
  if (ARTIFACT_MIMES.has(mime)) return 'artifact'
  if (mime === 'image/svg+xml' || ext === 'svg') return null
  return 'shared_file'
}

function releasePreview(attachment: Attachment) {
  if (attachment.kind === 'artifact' && attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl)
}

/** A file that didn't make it into the draft, shown by name (#872). */
interface UploadFailure {
  name: string
  reason: MessageKey
}

function uploadFailureReason(err: unknown): MessageKey {
  const status = err instanceof RoomFileUploadError ? err.status : null
  if (status === 413) return 'chat.attachmentReasonTooLarge'
  if (status === 415) return 'chat.attachmentReasonUnsupportedType'
  return 'chat.attachmentReasonUploadFailed'
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** Browsers hand a pasted screenshot over as ``image.png``; every paste
 * would then upsert the same room file. Give it the paste time instead.
 * Files copied from disk keep their own names. */
function namePastedFile(file: File, now = new Date()): File {
  const generic = /^image\.(\w+)$/.exec(file.name)
  if (!generic || !file.type.startsWith('image/')) return file
  const stamp = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}`
    + `-${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`
  return new File([file], `pasted-${stamp}.${generic[1]}`, {
    type: file.type,
    lastModified: file.lastModified,
  })
}

/** Only drags that carry files are ours; text drags keep editing. */
const carriesFiles = (types: readonly string[] | DOMStringList | undefined) =>
  Array.from(types ?? []).includes('Files')

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
  // Thumbnails are blob URLs; release each one when its chip goes away
  // and whatever is left when the composer unmounts.
  const attachmentsRef = useRef(attachments)
  attachmentsRef.current = attachments
  useEffect(() => () => attachmentsRef.current.forEach(releasePreview), [])
  const addAttachment = (next: Attachment) => {
    setAttachments(prev => {
      // Uploads dedupe on bytes, so the same file twice is one attachment.
      if (prev.some(a => a.kind === next.kind && a.id === next.id)) {
        releasePreview(next)
        return prev
      }
      return [...prev, next]
    })
  }
  const [pendingUploads, setPendingUploads] = useState(0)
  const uploading = pendingUploads > 0
  const [uploadFailures, setUploadFailures] = useState<UploadFailure[]>([])
  // dragenter/dragleave fire for every child the pointer crosses; count
  // them so the drop hint doesn't flicker while moving over the chips.
  const dragDepth = useRef(0)
  const [dragActive, setDragActive] = useState(false)
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
    const references: Record<string, unknown>[] = [
      ...dedupeSharedFileReferences([
        ...trackedFileReferences.current.map(f => f.reference),
        ...resolveFileReferencesInText(content, roomFiles),
        ...attachments.flatMap(a => a.kind !== 'shared_file' ? [] : [{
          type: 'shared_file' as const,
          id: a.id,
          name: a.filename,
          storage_name: a.storage_name,
          sha256: a.sha256,
          origin: 'attachment' as const,
        }]),
      ]),
      // The server rebuilds name/mime/size from the artifact row.
      ...attachments.flatMap(a => a.kind === 'artifact' ? [{ type: 'artifact', id: a.id }] : []),
    ]
    const metadata: Record<string, unknown> = {}
    if (mentions.length > 0) metadata.mentions = mentions
    if (references.length > 0) metadata.references = references
    onSend(content, Object.keys(metadata).length > 0 ? metadata : undefined)
    setValue('')
    clearDraft(draftKey)
    trackedMentions.current = []
    trackedFileReferences.current = []
    attachments.forEach(releasePreview)
    setAttachments([])
    setUploadFailures([])
    setMention(null)
    onTyping(false)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
  }

  const canAttach = Boolean(roomId) && !disabled

  // #872 — picking, dropping and pasting all land here. Files go up one
  // at a time: re-uploading a name upserts the room file, and parallel
  // upserts of the same name would race on the server.
  const uploadFiles = async (files: File[]) => {
    if (!roomId || files.length === 0) return
    setUploadFailures([])
    setPendingUploads(n => n + files.length)
    let uploadedAny = false
    for (const file of files) {
      try {
        const store = attachmentStore(file)
        if (store === null) throw new RoomFileUploadError(415, '')
        if (store === 'artifact') {
          const uploaded = await uploadRoomArtifact(roomId, file)
          const previewUrl = uploaded.mime.startsWith('image/') ? URL.createObjectURL(file) : undefined
          addAttachment({ kind: 'artifact', id: uploaded.id, filename: uploaded.filename, previewUrl })
        } else {
          const uploaded: RoomSharedFile = await uploadRoomFile(roomId, file)
          uploadedAny = true
          addAttachment({
            kind: 'shared_file',
            id: uploaded.id,
            filename: uploaded.filename,
            storage_name: uploaded.storage_name,
            sha256: uploaded.sha256,
          })
        }
      } catch (err) {
        setUploadFailures(prev => [...prev, { name: file.name, reason: uploadFailureReason(err) }])
      } finally {
        setPendingUploads(n => n - 1)
      }
    }
    if (uploadedAny) void refreshRoomFiles()
  }

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    // Reset the input so selecting the same file twice in a row
    // still fires ``change``.
    e.target.value = ''
    void uploadFiles(files)
  }

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? [])
    // Text-only clipboards paste as usual; a clipboard with files (a
    // screenshot, files copied in a file manager) attaches them instead.
    if (!canAttach || files.length === 0) return
    e.preventDefault()
    const now = new Date()
    void uploadFiles(files.map(file => namePastedFile(file, now)))
  }

  const handleDragEnter = (e: React.DragEvent) => {
    if (!canAttach || !carriesFiles(e.dataTransfer?.types)) return
    e.preventDefault()
    dragDepth.current += 1
    setDragActive(true)
  }

  const handleDragOver = (e: React.DragEvent) => {
    if (!canAttach || !carriesFiles(e.dataTransfer?.types)) return
    // Without this the browser opens the file instead of firing ``drop``.
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const handleDragLeave = () => {
    if (dragDepth.current === 0) return
    dragDepth.current -= 1
    if (dragDepth.current === 0) setDragActive(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    dragDepth.current = 0
    setDragActive(false)
    if (!canAttach || !carriesFiles(e.dataTransfer?.types)) return
    e.preventDefault()
    void uploadFiles(Array.from(e.dataTransfer.files ?? []))
  }

  const removeAttachment = (removed: Attachment) => {
    releasePreview(removed)
    setAttachments(prev => prev.filter(a => a !== removed))
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
    <div data-testid="message-composer" className="min-h-[var(--footer-min-height)] border-t border-[var(--color-border)] bg-[var(--color-surface)] py-3">
      <div
        data-testid="composer-column"
        className="chat-column relative flex flex-col gap-2"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {dragActive && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center rounded-[var(--radius-lg)] border-2 border-dashed border-[var(--color-brand-focus)] bg-[var(--color-surface)] text-caption text-[var(--color-brand-text)]"
          >
            <Paperclip className="mr-2 h-4 w-4" />
            {t('chat.dropFilesHere')}
          </div>
        )}
        {slashError && (
          <div
            data-testid="slash-error"
            className="text-xs text-[var(--color-destructive)]"
          >
            {slashError}
          </div>
        )}
        {(attachments.length > 0 || uploadFailures.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {attachments.map(a => (
              <span
                key={`${a.kind}-${a.id}`}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-0.5 text-xs text-[var(--color-foreground)]"
              >
                {a.kind === 'artifact' && a.previewUrl ? (
                  <img
                    src={a.previewUrl}
                    alt={a.filename}
                    className="h-5 w-5 rounded-[var(--radius-sm)] object-cover"
                  />
                ) : (
                  <Paperclip className="h-3 w-3 text-[var(--color-foreground-subtle)]" />
                )}
                <span className="max-w-[200px] truncate" title={a.filename}>
                  {a.filename}
                </span>
                <button
                  type="button"
                  onClick={() => removeAttachment(a)}
                  className="flex size-[var(--control-icon-size)] items-center justify-center text-[var(--color-foreground-subtle)] hover:text-[var(--color-foreground)]"
                  aria-label={t('chat.removeAttachment', { name: a.filename })}
                  title={t('chat.remove')}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            {uploadFailures.map((failure, index) => (
              <span
                key={`${failure.name}-${index}`}
                role="alert"
                className="text-caption text-[var(--color-destructive)]"
              >
                {`${failure.name} — ${t(failure.reason)}`}
              </span>
            ))}
          </div>
        )}
        <div data-testid="composer-box" className="relative flex w-full items-end gap-1 rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-1.5 transition-colors focus-within:border-[var(--color-brand-focus)] focus-within:ring-2 focus-within:ring-[var(--color-brand-focus)]/35">
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
                data-testid="composer-file-input"
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelected}
                accept={FILE_ACCEPT}
              />
              <button
                type="button"
                onClick={() => setAddMenuOpen(open => !open)}
                disabled={disabled || uploading}
                title={t('chat.addMenu')}
                aria-label={t('chat.addMenu')}
                aria-haspopup="menu"
                aria-expanded={addMenuOpen}
                className="flex size-[var(--control-icon-size)] items-center justify-center rounded-full text-[var(--color-foreground-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Plus className="h-[18px] w-[18px]" />
              </button>
              {addMenuOpen && (
                <div
                  role="menu"
                  aria-label={t('chat.addMenu')}
                  className="absolute bottom-full left-0 z-50 mb-2 w-44 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] py-1 shadow-deep"
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
            onPaste={handlePaste}
            disabled={disabled}
            placeholder={
              disabled
                ? t('chat.connecting')
                : placeholder ?? t('chat.messagePlaceholder')
            }
            aria-label={placeholder ?? t('chat.messageInputLabel')}
            rows={1}
            // #867 — autoResize sizes the box to the typed value only, so a
            // placeholder longer than the box wrapped inside one line of
            // height and showed half a second line. Keep it on one line
            // ending in an ellipsis: Chromium draws the ellipsis only when
            // both the empty box (:placeholder-shown) and ::placeholder are
            // nowrap/hidden/ellipsis, and drops it while focused (the text
            // is then clipped, still on one line). Typed text still wraps.
            className="min-h-[var(--control-icon-size)] flex-1 resize-none bg-transparent px-2 py-[calc((var(--control-icon-size)-1.5rem)/2)] text-base leading-6 md:text-sm text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] placeholder-shown:overflow-hidden placeholder-shown:text-ellipsis placeholder-shown:whitespace-nowrap placeholder:overflow-hidden placeholder:text-ellipsis placeholder:whitespace-nowrap focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={
              disabled || uploading || (!value.trim() && attachments.length === 0)
            }
            title={t('chat.sendMessage')}
            aria-label={t('chat.sendMessage')}
            className="flex size-[var(--control-icon-size)] shrink-0 items-center justify-center rounded-full bg-[var(--color-brand)] text-[var(--color-on-brand)] transition-colors hover:bg-[var(--color-brand-hover)] disabled:cursor-not-allowed disabled:bg-[var(--color-surface-hover)] disabled:text-[var(--color-foreground-subtle)]"
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
