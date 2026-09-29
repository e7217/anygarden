// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageInput from './MessageInput'
import type { RoomSharedFile } from '@/lib/roomFiles'

const files: RoomSharedFile[] = [
  {
    id: 'file-1',
    room_id: 'room-1',
    filename: 'spec.md',
    storage_name: 'spec.md',
    sha256: 'sha-file-1',
    size_bytes: 12,
    mime: 'text/markdown',
    uploaded_by: null,
    created_at: '2026-05-10T00:00:00Z',
  },
]

vi.mock('@/hooks/useRoomFiles', () => ({
  useRoomFiles: () => ({
    files,
    loading: false,
    error: null,
    refresh: vi.fn(),
    upload: vi.fn(),
    remove: vi.fn(),
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('MessageInput file references', () => {
  it('offers $ shared-file autocomplete and sends metadata.references', () => {
    const onSend = vi.fn()
    render(
      <MessageInput
        onSend={onSend}
        onTyping={vi.fn()}
        roomId="room-1"
      />,
    )

    const input = screen.getByPlaceholderText('Type a message... (@ to mention, # for rooms)')
    fireEvent.change(input, { target: { value: '$sp', selectionStart: 3 } })

    fireEvent.mouseDown(screen.getByText('spec.md'))
    expect(input).toHaveValue('$spec.md ')

    fireEvent.change(input, {
      target: { value: '$spec.md please review', selectionStart: 22 },
    })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSend).toHaveBeenCalledWith(
      '$spec.md please review',
      {
        references: [
          {
            type: 'shared_file',
            id: 'file-1',
            name: 'spec.md',
            storage_name: 'spec.md',
            sha256: 'sha-file-1',
            origin: 'inline',
          },
        ],
      },
    )
  })
})

describe('MessageInput @everyone and mention hint (#739)', () => {
  const users = [
    { id: 'pa', display: 'alpha', kind: 'agent' as const },
    { id: 'pb', display: 'beta', kind: 'agent' as const },
  ]
  const placeholder = 'Type a message... (@ to mention, # for rooms)'

  it('lists @everyone first and inserts the literal keyword', () => {
    const onSend = vi.fn()
    render(<MessageInput onSend={onSend} onTyping={vi.fn()} mentionUsers={users} />)
    const input = screen.getByPlaceholderText(placeholder)

    fireEvent.change(input, { target: { value: '@', selectionStart: 1 } })
    const everyone = screen.getByText('everyone')
    const alpha = screen.getByText('alpha')
    expect(
      everyone.compareDocumentPosition(alpha) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(screen.getByText('Ask every agent in this room')).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByText('everyone'))
    expect(input).toHaveValue('@everyone ')

    fireEvent.change(input, { target: { value: '@everyone 안녕', selectionStart: 12 } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('@everyone 안녕', undefined)
  })

  it('filters @everyone by the typed query', () => {
    render(<MessageInput onSend={vi.fn()} onTyping={vi.fn()} mentionUsers={users} />)
    const input = screen.getByPlaceholderText(placeholder)

    fireEvent.change(input, { target: { value: '@al', selectionStart: 3 } })
    expect(screen.queryByText('everyone')).not.toBeInTheDocument()
    expect(screen.getByText('alpha')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '@ev', selectionStart: 3 } })
    expect(screen.getByText('everyone')).toBeInTheDocument()
  })

  it('does not offer @everyone when the room has no agents', () => {
    render(
      <MessageInput
        onSend={vi.fn()}
        onTyping={vi.fn()}
        mentionUsers={[{ id: 'h1', display: 'human', kind: 'user' }]}
      />,
    )
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '@', selectionStart: 1 } })
    expect(screen.queryByText('everyone')).not.toBeInTheDocument()
  })

  it('shows the hint only for a non-empty draft without a mention', () => {
    render(
      <MessageInput
        onSend={vi.fn()}
        onTyping={vi.fn()}
        mentionUsers={users}
        showMentionHint
      />,
    )
    const input = screen.getByPlaceholderText(placeholder)
    const hint = 'Agents reply only when mentioned. Use @name or @everyone.'
    expect(screen.queryByText(hint)).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: '안녕', selectionStart: 2 } })
    expect(screen.getByText(hint)).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '@everyone 안녕', selectionStart: 12 } })
    expect(screen.queryByText(hint)).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: '@', selectionStart: 1 } })
    fireEvent.mouseDown(screen.getByText('alpha'))
    expect(input).toHaveValue('@alpha ')
    expect(screen.queryByText(hint)).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: '   ', selectionStart: 3 } })
    expect(screen.queryByText(hint)).not.toBeInTheDocument()
  })

  it('does not show the hint unless enabled', () => {
    render(<MessageInput onSend={vi.fn()} onTyping={vi.fn()} mentionUsers={users} />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '안녕', selectionStart: 2 } })
    expect(
      screen.queryByText('Agents reply only when mentioned. Use @name or @everyone.'),
    ).not.toBeInTheDocument()
  })
})
