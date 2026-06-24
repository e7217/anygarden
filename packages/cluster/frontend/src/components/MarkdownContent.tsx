import { Children, memo, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'
import Anser from 'anser'
import {
  resolveFileReferenceToken,
  type FileReferenceCandidate,
} from '@/lib/fileReferences'

const plugins: PluggableList = [remarkGfm]

// #290 Phase A — codex-cli / claude-code agents sometimes paste raw terminal
// captures into chat messages with SGR escape sequences embedded. Without
// rendering they look like noise. We tokenize the code-block body via anser
// and wrap each segment in a styled <span> rather than emitting HTML —
// anser's JSON path keeps the content as plain text strings, so React
// handles HTML escaping for us.
const ANSI_PATTERN = /\x1b\[[0-9;]*m/

function ansiTokenStyle(
  decorations: readonly string[],
  fg: string | null,
  bg: string | null,
): CSSProperties {
  const style: CSSProperties = {}
  if (fg) style.color = `rgb(${fg})`
  if (bg) style.backgroundColor = `rgb(${bg})`
  if (decorations.includes('bold')) style.fontWeight = 'bold'
  if (decorations.includes('italic')) style.fontStyle = 'italic'
  const lines: string[] = []
  if (decorations.includes('underline')) lines.push('underline')
  if (decorations.includes('strikethrough')) lines.push('line-through')
  if (lines.length > 0) style.textDecoration = lines.join(' ')
  // ``dim`` has no exact CSS analogue; 60% opacity matches xterm-style
  // renderers' conventional approximation.
  if (decorations.includes('dim')) style.opacity = 0.6
  return style
}

function renderAnsiSegments(text: string): ReactElement[] {
  const tokens = Anser.ansiToJson(text, { remove_empty: true })
  return tokens.map((tok, i) => (
    <span
      key={i}
      data-ansi-segment=""
      style={ansiTokenStyle(tok.decorations, tok.fg, tok.bg)}
    >
      {tok.content}
    </span>
  ))
}

// react-markdown v10 dropped the legacy ``inline`` prop on the code
// component. Detect fenced/indented blocks via either a multi-line body
// (only blocks produce ``\n`` in children) or the ``language-*`` className
// that GFM attaches to fenced blocks.
function isBlockCode(text: string, className: string | undefined): boolean {
  if (text.includes('\n')) return true
  if (className && /\blanguage-/.test(className)) return true
  return false
}

const defaultComponents: Components = {
  a: ({ children, href, ...props }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  ),
  code: ({ className, children, ...rest }) => {
    const text =
      typeof children === 'string' ? children : String(children ?? '')
    if (isBlockCode(text, className) && ANSI_PATTERN.test(text)) {
      return (
        <code className={className} {...rest}>
          {renderAnsiSegments(text)}
        </code>
      )
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    )
  },
}

interface MarkdownContentProps {
  content: string
  resolveUser?: (id: string) => string | undefined
  resolveRoom?: (id: string) => { name: string; id: string } | undefined
  fileReferenceCandidates?: FileReferenceCandidate[]
}

function renderInlineTokens(
  content: string,
  resolveUser?: (id: string) => string | undefined,
  resolveRoom?: (id: string) => { name: string; id: string } | undefined,
  fileReferenceCandidates: readonly FileReferenceCandidate[] = [],
  keyPrefix = 'inline',
): (string | ReactElement)[] {
  const parts: (string | ReactElement)[] = []
  const re = /<@user:([^>]+)>|<#room:([^>]+)>|(^|\s)\$([^\s$()]+)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = re.exec(content)) !== null) {
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }
    if (match[1]) {
      const name = resolveUser?.(match[1]) ?? '알 수 없는 사용자'
      parts.push(
        <span
          key={`${keyPrefix}-u-${match.index}`}
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand)] font-medium"
        >
          @{name}
        </span>
      )
    } else if (match[2]) {
      const room = resolveRoom?.(match[2])
      const roomName = room?.name ?? '알 수 없는 방'
      parts.push(
        <a
          key={`${keyPrefix}-r-${match.index}`}
          href={room ? `/rooms/${room.id}` : '#'}
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand)] font-medium hover:underline"
          onClick={(e) => {
            if (!room) e.preventDefault()
          }}
        >
          #{roomName}
        </a>
      )
    } else if (match[4]) {
      const prefix = match[3] ?? ''
      const rawToken = match[4]
      const resolved = resolveFileReferenceToken(rawToken, fileReferenceCandidates)
      if (!resolved) {
        parts.push(match[0])
      } else {
        if (prefix) parts.push(prefix)
        const storageName = resolved.candidate.storage_name
        parts.push(
          <span
            key={`${keyPrefix}-f-${match.index}`}
            data-file-reference={resolved.candidate.id}
            className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand)] font-medium"
            title={storageName ? `memory/shared/${storageName}` : resolved.candidate.name}
          >
            ${resolved.token}
          </span>,
        )
        if (resolved.suffix) parts.push(resolved.suffix)
      }
    }
    lastIndex = re.lastIndex
  }
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }
  return parts
}

function renderInlineChildren(
  children: ReactNode,
  resolveUser?: (id: string) => string | undefined,
  resolveRoom?: (id: string) => { name: string; id: string } | undefined,
  fileReferenceCandidates: readonly FileReferenceCandidate[] = [],
  keyPrefix = 'inline',
): ReactNode[] {
  return Children.toArray(children).flatMap((child, index) => {
    if (typeof child === 'string') {
      return renderInlineTokens(
        child,
        resolveUser,
        resolveRoom,
        fileReferenceCandidates,
        `${keyPrefix}-${index}`,
      )
    }
    return child
  })
}

function createComponents(
  resolveUser?: (id: string) => string | undefined,
  resolveRoom?: (id: string) => { name: string; id: string } | undefined,
  fileReferenceCandidates: readonly FileReferenceCandidate[] = [],
): Components {
  const inline = (children: ReactNode, keyPrefix: string) =>
    renderInlineChildren(
      children,
      resolveUser,
      resolveRoom,
      fileReferenceCandidates,
      keyPrefix,
    )

  return {
    ...defaultComponents,
    p: ({ children, ...props }) => <p {...props}>{inline(children, 'p')}</p>,
    li: ({ children, ...props }) => <li {...props}>{inline(children, 'li')}</li>,
    h1: ({ children, ...props }) => <h1 {...props}>{inline(children, 'h1')}</h1>,
    h2: ({ children, ...props }) => <h2 {...props}>{inline(children, 'h2')}</h2>,
    h3: ({ children, ...props }) => <h3 {...props}>{inline(children, 'h3')}</h3>,
    h4: ({ children, ...props }) => <h4 {...props}>{inline(children, 'h4')}</h4>,
    h5: ({ children, ...props }) => <h5 {...props}>{inline(children, 'h5')}</h5>,
    h6: ({ children, ...props }) => <h6 {...props}>{inline(children, 'h6')}</h6>,
    strong: ({ children, ...props }) => <strong {...props}>{inline(children, 'strong')}</strong>,
    em: ({ children, ...props }) => <em {...props}>{inline(children, 'em')}</em>,
    td: ({ children, ...props }) => <td {...props}>{inline(children, 'td')}</td>,
    th: ({ children, ...props }) => <th {...props}>{inline(children, 'th')}</th>,
  }
}

export default memo(function MarkdownContent({
  content,
  resolveUser,
  resolveRoom,
  fileReferenceCandidates = [],
}: MarkdownContentProps) {
  return (
    <div className="markdown-prose">
      <ReactMarkdown
        remarkPlugins={plugins}
        components={createComponents(resolveUser, resolveRoom, fileReferenceCandidates)}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
