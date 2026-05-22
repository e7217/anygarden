# Mention System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `@` 참여자 멘션과 `#` 방 멘션을 자동완성 드롭다운과 함께 구현, ID 기반 저장으로 이름 변경에 안전하게.

**Architecture:** 프론트엔드 중심 구현. `MessageInput`에 자동완성 팝오버를 추가하고, `MarkdownContent`에서 멘션 토큰을 렌더링. 서버의 기존 `parse_mentions`를 ID 기반 패턴(`<@user:id>`, `<#room:id>`)으로 업그레이드. WS 프로토콜 변경 없음 — `SendFrame.metadata`와 `MessageOut.metadata`가 이미 존재.

**Tech Stack:** React, TypeScript, Tailwind CSS, react-markdown (기존), Python/FastAPI (기존 서버)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `frontend/src/components/MentionPopover.tsx` | 자동완성 드롭다운 UI |
| Create | `frontend/src/lib/mentions.ts` | 멘션 파싱/직렬화 유틸리티 |
| Modify | `frontend/src/components/MessageInput.tsx` | `@`/`#` 감지, 팝오버 트리거 |
| Modify | `frontend/src/components/MarkdownContent.tsx` | 멘션 토큰 렌더링 |
| Modify | `frontend/src/hooks/useWebSocket.ts` | send에 metadata 전달 |
| Modify | `frontend/src/pages/ChatPage.tsx` | participants/rooms를 MessageInput에 전달 |
| Modify | `anygarden/orchestration/rules.py` | `parse_mentions` ID 기반 패턴 지원 |

---

### Task 1: 멘션 파싱/직렬화 유틸리티

**Files:**
- Create: `frontend/src/lib/mentions.ts`

- [ ] **Step 1: mentions.ts 유틸 생성**

```ts
// frontend/src/lib/mentions.ts

export interface Mention {
  type: 'user' | 'room'
  id: string
  display: string
}

/** content 문자열에서 <@user:id> / <#room:id> 토큰을 찾아 반환 */
const MENTION_RE = /<@user:([^>]+)>|<#room:([^>]+)>/g

export function parseMentionTokens(content: string): Mention[] {
  const mentions: Mention[] = []
  let m: RegExpExecArray | null
  MENTION_RE.lastIndex = 0
  while ((m = MENTION_RE.exec(content)) !== null) {
    if (m[1]) mentions.push({ type: 'user', id: m[1], display: '' })
    if (m[2]) mentions.push({ type: 'room', id: m[2], display: '' })
  }
  return mentions
}

/**
 * 자동완성에서 선택 시 content에 삽입할 토큰 생성.
 * 예: insertMentionToken('user', 'abc123') => '<@user:abc123>'
 */
export function insertMentionToken(type: 'user' | 'room', id: string): string {
  return type === 'user' ? `<@user:${id}>` : `<#room:${id}>`
}

/**
 * 전송 전에 content에서 멘션 토큰을 추출하여 metadata.mentions 배열 생성.
 */
export function extractMentionsMetadata(content: string): { type: string; id: string }[] {
  return parseMentionTokens(content).map(m => ({ type: m.type, id: m.id }))
}
```

- [ ] **Step 2: 커밋**

```bash
git add frontend/src/lib/mentions.ts
git commit -m "feat(mentions): add mention token parse/serialize utilities"
```

---

### Task 2: 서버 `parse_mentions` 업그레이드

**Files:**
- Modify: `anygarden-server/anygarden/orchestration/rules.py:63-75`

- [ ] **Step 1: 기존 테스트 확인**

```bash
cd anygarden-server && uv run pytest tests/ -k "mention" -v
```

- [ ] **Step 2: 테스트 작성**

`tests/test_rules.py`가 있으면 거기에 추가, 없으면 생성:

```python
# tests/test_mention_parsing.py
from anygarden.orchestration.rules import parse_mentions

def test_parse_id_based_user_mention():
    result = parse_mentions("Hello <@user:abc123> check this")
    assert result == [{"type": "user", "id": "abc123"}]

def test_parse_id_based_room_mention():
    result = parse_mentions("See <#room:xyz789> for details")
    assert result == [{"type": "room", "id": "xyz789"}]

def test_parse_mixed_mentions():
    result = parse_mentions("<@user:a1> said check <#room:r2>")
    assert result == [
        {"type": "user", "id": "a1"},
        {"type": "room", "id": "r2"},
    ]

def test_parse_no_mentions():
    result = parse_mentions("Just a normal message")
    assert result == []

def test_parse_legacy_at_mention():
    """기존 @Name 형식은 하위호환을 위해 name 리스트로 반환."""
    result = parse_mentions("Hey @Alice")
    assert result == [{"type": "legacy", "name": "Alice"}]
```

- [ ] **Step 3: 테스트 실행 — 실패 확인**

```bash
cd anygarden-server && uv run pytest tests/test_mention_parsing.py -v
```

Expected: FAIL — 현재 `parse_mentions`는 `list[str]`을 반환

- [ ] **Step 4: `parse_mentions` 업그레이드**

```python
# anygarden/orchestration/rules.py — Mention Parsing 섹션 교체

# ID-based mention tokens: <@user:id> and <#room:id>
_ID_MENTION_PATTERN = re.compile(r"<@user:([^>]+)>|<#room:([^>]+)>")
# Legacy @Name mentions (backward compat)
_LEGACY_MENTION_PATTERN = re.compile(r"(?<!\w)@([\w-]+)")


def parse_mentions(content: str) -> list[dict[str, str]]:
    """Extract mentions from a message.

    Supports two formats:
    - ID-based: ``<@user:abc123>`` → ``{"type": "user", "id": "abc123"}``
    - ID-based: ``<#room:xyz789>`` → ``{"type": "room", "id": "xyz789"}``
    - Legacy:   ``@Name``          → ``{"type": "legacy", "name": "Name"}``

    >>> parse_mentions("<@user:abc> and <#room:xyz>")
    [{'type': 'user', 'id': 'abc'}, {'type': 'room', 'id': 'xyz'}]
    >>> parse_mentions("Hey @Alice")
    [{'type': 'legacy', 'name': 'Alice'}]
    """
    mentions: list[dict[str, str]] = []
    for m in _ID_MENTION_PATTERN.finditer(content):
        if m.group(1):
            mentions.append({"type": "user", "id": m.group(1)})
        elif m.group(2):
            mentions.append({"type": "room", "id": m.group(2)})
    # Only fall back to legacy parsing when no ID-based mentions found
    if not mentions:
        for m in _LEGACY_MENTION_PATTERN.finditer(content):
            mentions.append({"type": "legacy", "name": m.group(1)})
    return mentions
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

```bash
cd anygarden-server && uv run pytest tests/test_mention_parsing.py -v
```

Expected: ALL PASS

- [ ] **Step 6: 기존 테스트 회귀 확인**

```bash
cd anygarden-server && uv run pytest -v
```

Expected: 기존 214개 테스트 전체 통과 (handler.py가 `parse_mentions` 반환 타입에 의존하므로 확인)

- [ ] **Step 7: 커밋**

```bash
cd anygarden-server && git add anygarden/orchestration/rules.py tests/test_mention_parsing.py
git commit -m "feat(server): upgrade parse_mentions to ID-based token format

Support <@user:id> and <#room:id> patterns alongside legacy @Name.
Returns list[dict] instead of list[str] for structured mention data."
```

---

### Task 3: MentionPopover 컴포넌트

**Files:**
- Create: `frontend/src/components/MentionPopover.tsx`

- [ ] **Step 1: MentionPopover 컴포넌트 생성**

```tsx
// frontend/src/components/MentionPopover.tsx
import { useEffect, useRef, useState } from 'react'

export interface MentionOption {
  id: string
  display: string
  kind: 'user' | 'agent' | 'room'
}

interface MentionPopoverProps {
  options: MentionOption[]
  query: string
  position: { top: number; left: number }
  onSelect: (option: MentionOption) => void
  onClose: () => void
}

export default function MentionPopover({
  options,
  query,
  position,
  onSelect,
  onClose,
}: MentionPopoverProps) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)

  const filtered = options.filter(o =>
    o.display.toLowerCase().includes(query.toLowerCase()),
  )

  // Reset selection when filtered list changes
  useEffect(() => { setSelectedIndex(0) }, [query])

  // Scroll selected item into view
  useEffect(() => {
    const el = listRef.current?.children[selectedIndex] as HTMLElement | undefined
    el?.scrollIntoView({ block: 'nearest' })
  }, [selectedIndex])

  // Keyboard navigation is handled by MessageInput via onKeyDown interception.
  // This component exposes selectedIndex and filtered list for that purpose.

  if (filtered.length === 0) return null

  return (
    <div
      ref={listRef}
      className="absolute z-50 max-h-48 w-56 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-white shadow-sm"
      style={{ bottom: position.top, left: position.left }}
    >
      {filtered.map((option, i) => (
        <button
          key={option.id}
          className={`flex w-full items-center gap-2 px-3 py-1.5 text-sm text-left transition-colors ${
            i === selectedIndex
              ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand)]'
              : 'text-[var(--color-foreground)] hover:bg-black/[0.03]'
          }`}
          onMouseDown={(e) => { e.preventDefault(); onSelect(option) }}
          onMouseEnter={() => setSelectedIndex(i)}
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

/**
 * MentionPopover의 키보드 내비게이션을 외부에서 제어하기 위한 훅.
 * MessageInput의 onKeyDown에서 호출.
 */
export function useMentionKeyboard(
  filteredCount: number,
  selectedIndex: number,
  setSelectedIndex: (i: number) => void,
) {
  return (e: React.KeyboardEvent): 'select' | 'close' | 'handled' | null => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIndex(Math.min(selectedIndex + 1, filteredCount - 1))
      return 'handled'
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIndex(Math.max(selectedIndex - 1, 0))
      return 'handled'
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      return 'select'
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      return 'close'
    }
    return null
  }
}
```

- [ ] **Step 2: 커밋**

```bash
git add frontend/src/components/MentionPopover.tsx
git commit -m "feat(frontend): add MentionPopover component for autocomplete"
```

---

### Task 4: MessageInput 멘션 통합

**Files:**
- Modify: `frontend/src/components/MessageInput.tsx`
- Modify: `frontend/src/pages/ChatPage.tsx`
- Modify: `frontend/src/hooks/useWebSocket.ts`

- [ ] **Step 1: useWebSocket.send에 metadata 지원 추가**

`useWebSocket.ts`의 `send` 콜백 수정:

```ts
// 기존:
const send = useCallback((content: string) => {
  wsRef.current?.send(JSON.stringify({ type: 'send', content }));
}, []);

// 변경:
const send = useCallback((content: string, metadata?: Record<string, unknown>) => {
  const frame: Record<string, unknown> = { type: 'send', content }
  if (metadata && Object.keys(metadata).length > 0) frame.metadata = metadata
  wsRef.current?.send(JSON.stringify(frame));
}, []);
```

반환 타입도 일치시킴:
```ts
return { messages, connected, typingUsers, send, sendTyping };
```

- [ ] **Step 2: MessageInput props 확장 & 멘션 로직 추가**

`MessageInput.tsx` 전체 교체:

```tsx
import { useState, useRef, useCallback, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Send } from 'lucide-react'
import MentionPopover, { type MentionOption } from '@/components/MentionPopover'
import { insertMentionToken, extractMentionsMetadata } from '@/lib/mentions'

interface MessageInputProps {
  onSend: (content: string, metadata?: Record<string, unknown>) => void
  onTyping: (isTyping: boolean) => void
  disabled?: boolean
  /** 현재 방 참여자 목록 (@ 자동완성) */
  mentionUsers?: MentionOption[]
  /** 전체 방 목록 (# 자동완성) */
  mentionRooms?: MentionOption[]
}

interface MentionState {
  type: '@' | '#'
  startIndex: number       // trigger 문자 위치
  query: string
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

  const autoResize = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const maxHeight = 5 * 24
    el.style.height = Math.min(el.scrollHeight, maxHeight) + 'px'
  }, [])

  useEffect(() => { autoResize() }, [value, autoResize])

  const currentOptions = mention?.type === '@' ? mentionUsers : mentionRooms
  const filtered = mention
    ? currentOptions.filter(o => o.display.toLowerCase().includes(mention.query.toLowerCase()))
    : []

  // Reset selection on query change
  useEffect(() => { setSelectedIndex(0) }, [mention?.query])

  const closeMention = useCallback(() => { setMention(null) }, [])

  const selectMention = useCallback((option: MentionOption) => {
    if (!mention) return
    const tokenType = mention.type === '@' ? 'user' : 'room'
    const token = insertMentionToken(tokenType, option.id)
    // Replace: from trigger char through query with token + space
    const before = value.slice(0, mention.startIndex)
    const after = value.slice(mention.startIndex + 1 + mention.query.length)
    const newValue = before + token + ' ' + after
    setValue(newValue)
    setMention(null)
    // Restore focus
    setTimeout(() => {
      const el = textareaRef.current
      if (el) {
        const cursorPos = before.length + token.length + 1
        el.setSelectionRange(cursorPos, cursorPos)
        el.focus()
      }
    }, 0)
  }, [mention, value])

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    const mentions = extractMentionsMetadata(trimmed)
    const metadata = mentions.length > 0 ? { mentions } : undefined
    onSend(trimmed, metadata)
    setValue('')
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
    // Position popover above the textarea
    setPopoverPos({ top: el.offsetHeight + 4, left: 0 })
  }, [])

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value
    setValue(newValue)
    onTyping(true)
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    typingTimeoutRef.current = setTimeout(() => onTyping(false), 2000)

    // Check for mention trigger
    const cursorPos = e.target.selectionStart
    const textUpToCursor = newValue.slice(0, cursorPos)

    // Find the last @ or # that starts a mention
    const atMatch = textUpToCursor.match(/(?:^|\s)@([^\s]*)$/)
    const hashMatch = textUpToCursor.match(/(?:^|\s)#([^\s]*)$/)

    if (atMatch) {
      const query = atMatch[1]
      const startIndex = cursorPos - query.length - 1  // -1 for @
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
      <div className="relative flex items-end gap-2">
        {mention && filtered.length > 0 && (
          <MentionPopover
            options={filtered}
            query={mention.query}
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
```

- [ ] **Step 3: MentionPopover에 selectedIndex prop 추가**

`MentionPopover.tsx` 수정 — 내부 state 대신 외부에서 제어:

```tsx
interface MentionPopoverProps {
  options: MentionOption[]
  query: string
  position: { top: number; left: number }
  selectedIndex: number          // 추가
  onSelect: (option: MentionOption) => void
  onClose: () => void
}

export default function MentionPopover({
  options, query, position, selectedIndex, onSelect, onClose,
}: MentionPopoverProps) {
  const listRef = useRef<HTMLDivElement>(null)

  // 내부 selectedIndex state 제거 — prop으로 받음

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
          onMouseEnter={() => {}}
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
```

`useMentionKeyboard` export를 제거 (MessageInput에서 직접 처리).

- [ ] **Step 4: ChatPage에서 mentionUsers/mentionRooms prop 전달**

`ChatPage.tsx` 수정 — `MessageInput`에 props 추가:

```tsx
// ChatPage.tsx — import 추가
import type { MentionOption } from '@/components/MentionPopover'

// ChatPage.tsx — return문 안 MessageInput 교체 (기존 line 219-223)
// 기존:
<MessageInput
  onSend={send}
  onTyping={sendTyping}
  disabled={!connected}
/>

// 변경:
<MessageInput
  onSend={send}
  onTyping={sendTyping}
  disabled={!connected}
  mentionUsers={Object.values(participants).map(p => ({
    id: p.id,
    display: p.display_name,
    kind: (p.kind === 'agent' ? 'agent' : 'user') as 'user' | 'agent',
  }))}
  mentionRooms={Object.values(rooms).flat().map(r => ({
    id: r.id,
    display: r.name,
    kind: 'room' as const,
  }))}
/>
```

- [ ] **Step 5: 프론트엔드 빌드 확인**

```bash
cd anygarden-server/frontend && npm run build
```

Expected: 빌드 성공, 타입 에러 없음

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/MessageInput.tsx frontend/src/components/MentionPopover.tsx frontend/src/hooks/useWebSocket.ts frontend/src/pages/ChatPage.tsx
git commit -m "feat(frontend): integrate mention autocomplete into MessageInput

@ triggers participant list, # triggers room list.
Keyboard navigation (arrows, Enter, Esc) and mouse selection.
Sends metadata.mentions alongside message content."
```

---

### Task 5: MarkdownContent 멘션 렌더링

**Files:**
- Modify: `frontend/src/components/MarkdownContent.tsx`
- Modify: `frontend/src/components/MessageBubble.tsx`

- [ ] **Step 1: MarkdownContent에 멘션 토큰 렌더링 추가**

```tsx
// MarkdownContent.tsx 전체 교체
import { memo, useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'

const plugins: PluggableList = [remarkGfm]

interface NameResolver {
  resolveUser?: (id: string) => string | undefined
  resolveRoom?: (id: string) => { name: string; id: string } | undefined
}

/**
 * <@user:id> → styled span, <#room:id> → clickable link.
 * 토큰을 React elements로 교체한 뒤 markdown에 전달.
 */
function renderMentions(
  content: string,
  resolvers: NameResolver,
): (string | JSX.Element)[] {
  const parts: (string | JSX.Element)[] = []
  const re = /<@user:([^>]+)>|<#room:([^>]+)>/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = re.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }
    if (match[1]) {
      // User mention
      const name = resolvers.resolveUser?.(match[1]) ?? '알 수 없는 사용자'
      parts.push(
        <span
          key={`u-${match.index}`}
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand)] font-medium"
        >
          @{name}
        </span>
      )
    } else if (match[2]) {
      // Room mention
      const room = resolvers.resolveRoom?.(match[2])
      const roomName = room?.name ?? '알 수 없는 방'
      parts.push(
        <a
          key={`r-${match.index}`}
          href={room ? `/rooms/${room.id}` : '#'}
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand)] font-medium hover:underline"
          onClick={(e) => {
            if (!room) e.preventDefault()
          }}
        >
          #{roomName}
        </a>
      )
    }
    lastIndex = re.lastIndex
  }
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }
  return parts
}

const defaultComponents: Components = {
  a: ({ children, href, ...props }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  ),
}

interface MarkdownContentProps {
  content: string
  resolveUser?: (id: string) => string | undefined
  resolveRoom?: (id: string) => { name: string; id: string } | undefined
}

export default memo(function MarkdownContent({
  content,
  resolveUser,
  resolveRoom,
}: MarkdownContentProps) {
  // If no mention tokens exist, render plain markdown (fast path)
  const hasMentions = content.includes('<@user:') || content.includes('<#room:')

  if (!hasMentions) {
    return (
      <div className="markdown-prose">
        <ReactMarkdown remarkPlugins={plugins} components={defaultComponents}>
          {content}
        </ReactMarkdown>
      </div>
    )
  }

  // Split content into lines, process mention tokens per line,
  // render non-mention parts as markdown and mention tokens as spans.
  const parts = renderMentions(content, { resolveUser, resolveRoom })

  return (
    <div className="markdown-prose">
      {parts.map((part, i) =>
        typeof part === 'string' ? (
          <ReactMarkdown key={i} remarkPlugins={plugins} components={defaultComponents}>
            {part}
          </ReactMarkdown>
        ) : (
          part
        ),
      )}
    </div>
  )
})
```

- [ ] **Step 2: MessageBubble에서 resolvers 전달**

```tsx
// MessageBubble.tsx — MarkdownContent 호출 수정 (2곳)
// import 추가
import { useRooms } from '@/hooks/useRooms'
import { useNavigate } from 'react-router-dom'

// 컴포넌트 내부에 추가:
const { rooms } = useRooms()
const navigate = useNavigate()

const resolveUser = (id: string) => participants[id]?.display_name
const resolveRoom = (id: string) => {
  for (const projectRooms of Object.values(rooms)) {
    const found = projectRooms.find(r => r.id === id)
    if (found) return { name: found.name, id: found.id }
  }
  return undefined
}

// MarkdownContent 사용부 (2곳 — isMine과 other) 변경:
<MarkdownContent
  content={message.content}
  resolveUser={resolveUser}
  resolveRoom={resolveRoom}
/>
```

- [ ] **Step 3: 빌드 확인**

```bash
cd anygarden-server/frontend && npm run build
```

Expected: 빌드 성공

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/MarkdownContent.tsx frontend/src/components/MessageBubble.tsx
git commit -m "feat(frontend): render mention tokens in message bubbles

<@user:id> renders as styled @name span.
<#room:id> renders as clickable #room link.
Resolvers use participants map and useRooms context."
```

---

### Task 6: 서버 테스트 회귀 확인 & 전체 빌드 검증

**Files:** (no changes)

- [ ] **Step 1: 서버 전체 테스트**

```bash
cd anygarden-server && uv run pytest -v
```

Expected: 전체 통과

- [ ] **Step 2: 프론트엔드 빌드**

```bash
cd anygarden-server/frontend && npm run build
```

Expected: 빌드 성공

- [ ] **Step 3: dev server 실행 & 수동 확인**

```bash
cd anygarden-server && uv run anygarden-server --host 0.0.0.0 --port 8001 &
cd anygarden-server/frontend && npm run dev
```

브라우저에서 `http://localhost:5173`에서 확인:
- [ ] 채팅방 입장 후 `@` 입력 → 참여자 드롭다운 표시
- [ ] 방향키로 이동, Enter로 선택 → `<@user:id>` 토큰 삽입
- [ ] `#` 입력 → 방 목록 드롭다운 표시
- [ ] 메시지 전송 후 멘션이 `@이름` / `#방이름` 스타일로 렌더링

- [ ] **Step 4: 최종 커밋 (필요 시)**

남은 수정 사항이 있으면 커밋.
