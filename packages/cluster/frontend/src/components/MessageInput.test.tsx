// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageInput from './MessageInput'
import { RoomFileUploadError, type RoomSharedFile } from '@/lib/roomFiles'

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

const uploadRoomFile = vi.hoisted(() => vi.fn())
vi.mock('@/lib/roomFiles', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/roomFiles')>()),
  uploadRoomFile,
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

describe('MessageInput drop and paste attachments (#872)', () => {
  const placeholder = 'Type a message... (@ to mention, # for rooms)'

  function uploadedRow(file: File): RoomSharedFile {
    return {
      id: `id-${file.name}`,
      room_id: 'room-1',
      filename: file.name,
      storage_name: file.name,
      sha256: `sha-${file.name}`,
      size_bytes: file.size,
      mime: file.type,
      uploaded_by: null,
      created_at: '2026-10-16T00:00:00Z',
    }
  }

  function renderComposer(onSend = vi.fn()) {
    uploadRoomFile.mockImplementation(async (_roomId: string, file: File) => uploadedRow(file))
    render(<MessageInput onSend={onSend} onTyping={vi.fn()} roomId="room-1" />)
    return { onSend, input: screen.getByPlaceholderText(placeholder) }
  }

  const textFile = (name: string) => new File(['hello'], name, { type: 'text/plain' })

  it('uploads every dropped file and lists each as an attachment', async () => {
    const { onSend, input } = renderComposer()
    const column = screen.getByTestId('composer-column')
    const dropped = [textFile('a.txt'), textFile('b.txt')]

    fireEvent.dragEnter(column, { dataTransfer: { types: ['Files'], files: dropped } })
    expect(screen.getByText('Drop files to attach')).toBeInTheDocument()
    fireEvent.drop(column, { dataTransfer: { types: ['Files'], files: dropped } })

    expect(await screen.findByText('b.txt')).toBeInTheDocument()
    expect(screen.getByText('a.txt')).toBeInTheDocument()
    expect(screen.queryByText('Drop files to attach')).not.toBeInTheDocument()
    expect(uploadRoomFile).toHaveBeenCalledTimes(2)
    expect(uploadRoomFile.mock.calls.map(call => (call[1] as File).name)).toEqual(['a.txt', 'b.txt'])

    fireEvent.keyDown(input, { key: 'Enter' })
    const metadata = onSend.mock.calls[0][1] as { references: { id: string }[] }
    expect(metadata.references.map(ref => ref.id)).toEqual(['id-a.txt', 'id-b.txt'])
  })

  it('ignores a drag that carries no files', () => {
    renderComposer()
    const column = screen.getByTestId('composer-column')
    fireEvent.dragEnter(column, { dataTransfer: { types: ['text/plain'], files: [] } })
    expect(screen.queryByText('Drop files to attach')).not.toBeInTheDocument()
  })

  it('uploads a pasted file instead of pasting it as text', async () => {
    const { input } = renderComposer()
    const notPrevented = fireEvent.paste(input, {
      clipboardData: { types: ['Files'], files: [textFile('notes.txt')] },
    })
    expect(notPrevented).toBe(false)
    expect(await screen.findByText('notes.txt')).toBeInTheDocument()
  })

  it('leaves a text-only paste to the textarea', () => {
    const { input } = renderComposer()
    const notPrevented = fireEvent.paste(input, {
      clipboardData: { types: ['text/plain'], files: [] },
    })
    expect(notPrevented).toBe(true)
    expect(uploadRoomFile).not.toHaveBeenCalled()
  })

  it('names a pasted screenshot after the moment it was pasted', async () => {
    const { input } = renderComposer()
    const shot = new File(['png'], 'image.png', { type: 'image/png' })
    fireEvent.paste(input, { clipboardData: { types: ['Files'], files: [shot] } })

    await waitFor(() => expect(uploadRoomFile).toHaveBeenCalledTimes(1))
    const sent = uploadRoomFile.mock.calls[0][1] as File
    expect(sent.name).toMatch(/^pasted-\d{8}-\d{6}\.png$/)
    expect(sent.type).toBe('image/png')
  })

  it('reports a rejected file by name and keeps the rest', async () => {
    renderComposer()
    uploadRoomFile.mockImplementation(async (_roomId: string, file: File) => {
      if (file.name === 'photo.png') throw new RoomFileUploadError(415, '{"detail":"mime not allowed"}')
      return uploadedRow(file)
    })
    const column = screen.getByTestId('composer-column')
    fireEvent.drop(column, {
      dataTransfer: {
        types: ['Files'],
        files: [new File(['x'], 'photo.png', { type: 'image/png' }), textFile('ok.txt')],
      },
    })

    expect(await screen.findByText('ok.txt')).toBeInTheDocument()
    expect(screen.getByText('photo.png — file type not allowed')).toBeInTheDocument()
    expect(screen.queryByText(/mime not allowed/)).not.toBeInTheDocument()
  })

  it('attaches several files picked at once', async () => {
    renderComposer()
    const picker = screen.getByTestId('composer-file-input') as HTMLInputElement
    expect(picker.multiple).toBe(true)
    fireEvent.change(picker, { target: { files: [textFile('one.txt'), textFile('two.txt')] } })
    expect(await screen.findByText('two.txt')).toBeInTheDocument()
    expect(screen.getByText('one.txt')).toBeInTheDocument()
  })
})
