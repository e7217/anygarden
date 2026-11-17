import { Children, memo, useMemo, type CSSProperties, type HTMLAttributes, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'
import Anser from 'anser'
import AuthenticatedArtifactLink from '@/components/AuthenticatedArtifactLink'
import {
  resolveFileReferenceToken,
  type FileReferenceCandidate,
} from '@/lib/fileReferences'
import {
  resolveLocalFileHref,
  type ArtifactReference,
  type LinkedFile,
} from '@/lib/messageAttachments'

const plugins: PluggableList = [remarkGfm]

// #1017 — keep ``file:`` links so the link renderer can show them as a
// local path (text) instead of an empty link; other URLs are sanitised as
// before.
function urlTransform(url: string): string {
  return /^file:/i.test(url) ? url : defaultUrlTransform(url)
}

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
  a: ({ children, href, node: _node, ...props }) => (
    <AuthenticatedArtifactLink href={href} {...props}>
      {children}
    </AuthenticatedArtifactLink>
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
  fileReferenceCandidates?: readonly FileReferenceCandidate[]
  /** #964 — the message's attached files. A link to an agent's
   * ``memory/outbox/<room>/<name>`` opens the attached file of that name;
   * without one it is shown as text instead of a broken link. */
  references?: readonly ArtifactReference[]
  /** #1017 — the reply's links to local files and the files attached for
   * them. Any other local path is shown as text. */
  linkedFiles?: readonly LinkedFile[]
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
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand-text)] font-medium"
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
          className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-link)] font-medium hover:underline"
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
            className="inline-flex items-center rounded-[3px] bg-[var(--color-brand)]/10 px-1 text-[var(--color-brand-text)] font-medium"
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

// A message's markdown sits inside the page, under the room's own
// headings, so its ``#``/``##`` must not outrank them: levels shift down
// two (h1→h3 … capped at h6). Its look follows the source level through
// ``data-md-level`` (``.markdown-prose`` in index.css), not the tag.
function MessageHeading({
  level,
  children,
  ...props
}: { level: number; children?: ReactNode } & HTMLAttributes<HTMLHeadingElement>) {
  const Tag = `h${Math.min(level + 2, 6)}` as 'h3' | 'h4' | 'h5' | 'h6'
  return (
    <Tag
      {...props}
      data-md-level={level}
    >
      {children}
    </Tag>
  )
}

function createComponents(
  resolveUser?: (id: string) => string | undefined,
  resolveRoom?: (id: string) => { name: string; id: string } | undefined,
  fileReferenceCandidates: readonly FileReferenceCandidate[] = [],
  references: readonly ArtifactReference[] = [],
  linkedFiles: readonly LinkedFile[] = [],
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
    a: ({ children, href, node: _node, ...props }) => {
      const local = resolveLocalFileHref(href, references, linkedFiles)
      if (local === null) {
        return (
          <AuthenticatedArtifactLink href={href} {...props}>
            {children}
          </AuthenticatedArtifactLink>
        )
      }
      if (local.href === null) {
        return <span data-local-link-unresolved="" title={href}>{children}</span>
      }
      return (
        <AuthenticatedArtifactLink {...props} href={local.href}>
          {children}
        </AuthenticatedArtifactLink>
      )
    },
    p: ({ children, node: _node, ...props }) => <p {...props}>{inline(children, 'p')}</p>,
    li: ({ children, node: _node, ...props }) => <li {...props}>{inline(children, 'li')}</li>,
    h1: ({ children, node: _node, ...props }) => <MessageHeading level={1} {...props}>{inline(children, 'h1')}</MessageHeading>,
    h2: ({ children, node: _node, ...props }) => <MessageHeading level={2} {...props}>{inline(children, 'h2')}</MessageHeading>,
    h3: ({ children, node: _node, ...props }) => <MessageHeading level={3} {...props}>{inline(children, 'h3')}</MessageHeading>,
    h4: ({ children, node: _node, ...props }) => <MessageHeading level={4} {...props}>{inline(children, 'h4')}</MessageHeading>,
    h5: ({ children, node: _node, ...props }) => <MessageHeading level={5} {...props}>{inline(children, 'h5')}</MessageHeading>,
    h6: ({ children, node: _node, ...props }) => <MessageHeading level={6} {...props}>{inline(children, 'h6')}</MessageHeading>,
    strong: ({ children, node: _node, ...props }) => <strong {...props}>{inline(children, 'strong')}</strong>,
    em: ({ children, node: _node, ...props }) => <em {...props}>{inline(children, 'em')}</em>,
    td: ({ children, node: _node, ...props }) => <td {...props}>{inline(children, 'td')}</td>,
    th: ({ children, node: _node, ...props }) => <th {...props}>{inline(children, 'th')}</th>,
  }
}

// Shared empty defaults keep the memo below stable when a prop is omitted.
const NO_CANDIDATES: readonly FileReferenceCandidate[] = []
const NO_REFERENCES: readonly ArtifactReference[] = []
const NO_LINKED_FILES: readonly LinkedFile[] = []

export default memo(function MarkdownContent({
  content,
  resolveUser,
  resolveRoom,
  fileReferenceCandidates = NO_CANDIDATES,
  references = NO_REFERENCES,
  linkedFiles = NO_LINKED_FILES,
}: MarkdownContentProps) {
  // Rebuilding the renderer map makes react-markdown remount every
  // element, so build it only when an input it closes over changes.
  const components = useMemo(
    () => createComponents(resolveUser, resolveRoom, fileReferenceCandidates, references, linkedFiles),
    [resolveUser, resolveRoom, fileReferenceCandidates, references, linkedFiles],
  )
  return (
    <div className="markdown-prose">
      <ReactMarkdown
        remarkPlugins={plugins}
        urlTransform={urlTransform}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
