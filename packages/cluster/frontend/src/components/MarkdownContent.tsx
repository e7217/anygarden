import { memo, type CSSProperties, type ReactElement } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'
import Anser from 'anser'

const plugins: PluggableList = [remarkGfm]

// #290 Phase A — codex / claude-code agents sometimes paste raw terminal
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
}

function renderMentions(
  content: string,
  resolveUser?: (id: string) => string | undefined,
  resolveRoom?: (id: string) => { name: string; id: string } | undefined,
): (string | ReactElement)[] {
  const parts: (string | ReactElement)[] = []
  const re = /<@user:([^>]+)>|<#room:([^>]+)>/g
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
          key={`u-${match.index}`}
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

export default memo(function MarkdownContent({
  content,
  resolveUser,
  resolveRoom,
}: MarkdownContentProps) {
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

  const parts = renderMentions(content, resolveUser, resolveRoom)

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
