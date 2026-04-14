import { memo, type ReactElement } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { PluggableList } from 'unified'

const plugins: PluggableList = [remarkGfm]

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
