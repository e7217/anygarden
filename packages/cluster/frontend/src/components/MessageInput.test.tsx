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

const uploadRoomArtifact = vi.hoisted(() => vi.fn())
vi.mock('@/lib/roomArtifacts', async importOriginal => ({
  ...(await importOriginal<typeof import('@/lib/roomArtifacts')>()),
  uploadRoomArtifact,
}))

// jsdom has no blob URLs; image chips use them for thumbnails (#873).
URL.createObjectURL = vi.fn(() => 'blob:preview')
URL.revokeObjectURL = vi.fn()

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
    uploadRoomArtifact.mockImplementation(async (_roomId: string, file: File) => ({
      ...uploadedRow(file),
      id: `art-${file.name}`,
      produced_by_agent_id: null,
    }))
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

    await waitFor(() => expect(uploadRoomArtifact).toHaveBeenCalledTimes(1))
    const sent = uploadRoomArtifact.mock.calls[0][1] as File
    expect(sent.name).toMatch(/^pasted-\d{8}-\d{6}\.png$/)
    expect(sent.type).toBe('image/png')
  })

  it('reports a rejected file by name and keeps the rest', async () => {
    renderComposer()
    uploadRoomFile.mockImplementation(async (_roomId: string, file: File) => {
      if (file.name === 'tool.sh') throw new RoomFileUploadError(415, '{"detail":"mime not allowed"}')
      if (file.name === 'huge.txt') throw new RoomFileUploadError(413, '')
      return uploadedRow(file)
    })
    const column = screen.getByTestId('composer-column')
    fireEvent.drop(column, {
      dataTransfer: {
        types: ['Files'],
        files: [
          new File(['x'], 'tool.sh', { type: 'application/x-sh' }),
          textFile('huge.txt'),
          textFile('ok.txt'),
        ],
      },
    })

    expect(await screen.findByText('ok.txt')).toBeInTheDocument()
    expect(screen.getByText('tool.sh — file type not allowed')).toBeInTheDocument()
    expect(screen.getByText('huge.txt — file too large')).toBeInTheDocument()
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

describe('MessageInput image attachments (#873)', () => {
  const placeholder = 'Type a message... (@ to mention, # for rooms)'

  function renderComposer() {
    const onSend = vi.fn()
    uploadRoomFile.mockImplementation(async (_roomId: string, file: File) => ({
      id: `file-${file.name}`,
      room_id: 'room-1',
      filename: file.name,
      storage_name: file.name,
      sha256: `sha-${file.name}`,
      size_bytes: file.size,
      mime: file.type,
      uploaded_by: null,
      created_at: '2026-10-16T00:00:00Z',
    }))
    uploadRoomArtifact.mockImplementation(async (_roomId: string, file: File) => ({
      id: `art-${file.name}`,
      room_id: 'room-1',
      produced_by_agent_id: null,
      filename: file.name,
      sha256: `sha-${file.name}`,
      size_bytes: file.size,
      mime: file.type,
      created_at: '2026-10-16T00:00:00Z',
    }))
    render(<MessageInput onSend={onSend} onTyping={vi.fn()} roomId="room-1" />)
    return { onSend, input: screen.getByPlaceholderText(placeholder) }
  }

  const drop = (files: File[]) =>
    fireEvent.drop(screen.getByTestId('composer-column'), {
      dataTransfer: { types: ['Files'], files },
    })

  it('stores images and PDFs as artifacts and text as shared files', async () => {
    const { onSend, input } = renderComposer()
    drop([
      new File(['png'], 'shot.png', { type: 'image/png' }),
      new File(['%PDF'], 'report.pdf', { type: 'application/pdf' }),
      new File(['hi'], 'notes.txt', { type: 'text/plain' }),
    ])

    expect(await screen.findByText('notes.txt')).toBeInTheDocument()
    expect(uploadRoomArtifact.mock.calls.map(call => (call[1] as File).name)).toEqual(['shot.png', 'report.pdf'])
    expect(uploadRoomFile.mock.calls.map(call => (call[1] as File).name)).toEqual(['notes.txt'])

    fireEvent.keyDown(input, { key: 'Enter' })
    const [content, metadata] = onSend.mock.calls[0]
    expect(content).toBe('📎 3 files')
    expect(metadata.references).toEqual([
      {
        type: 'shared_file',
        id: 'file-notes.txt',
        name: 'notes.txt',
        storage_name: 'notes.txt',
        sha256: 'sha-notes.txt',
        origin: 'attachment',
      },
      { type: 'artifact', id: 'art-shot.png' },
      { type: 'artifact', id: 'art-report.pdf' },
    ])
  })

  it('shows a thumbnail for an attached image', async () => {
    renderComposer()
    drop([new File(['png'], 'shot.png', { type: 'image/png' })])
    expect(await screen.findByRole('img', { name: 'shot.png' })).toBeInTheDocument()
  })

  it('recognises an image by extension when the browser gives no type', async () => {
    renderComposer()
    drop([new File(['jpg'], 'photo.JPG', { type: '' })])
    await waitFor(() => expect(uploadRoomArtifact).toHaveBeenCalledTimes(1))
    expect(uploadRoomFile).not.toHaveBeenCalled()
  })

  it('refuses SVG without uploading it', async () => {
    renderComposer()
    drop([new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' })])
    expect(await screen.findByText('logo.svg — file type not allowed')).toBeInTheDocument()
    expect(uploadRoomArtifact).not.toHaveBeenCalled()
    expect(uploadRoomFile).not.toHaveBeenCalled()
  })

  it('lets the file picker offer images and PDFs', () => {
    renderComposer()
    const accept = (screen.getByTestId('composer-file-input') as HTMLInputElement).accept
    for (const type of ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf']) {
      expect(accept).toContain(type)
    }
    expect(accept).not.toContain('svg')
  })
})

describe('MessageInput slash commands (#940)', () => {
  const placeholder = 'Type a message... (@ to mention, # for rooms)'

  it('[MSG-10] suggests commands while the command word is typed and fills one in', () => {
    render(<MessageInput onSend={vi.fn()} onTyping={vi.fn()} roomId="room-1" onSlashForm={vi.fn(() => null)} />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/', selectionStart: 1 } })
    expect(screen.getByText('/task @agent title')).toBeInTheDocument()
    expect(screen.getByText('/routine @agent description')).toBeInTheDocument()
    expect(screen.getByText('/goal description')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '/ro', selectionStart: 3 } })
    expect(screen.queryByText('/task @agent title')).not.toBeInTheDocument()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(input).toHaveValue('/routine ')
  })

  it('[WSP-07] says in the suggestion who would write the draft', () => {
    render(
      <MessageInput
        onSend={vi.fn()} onTyping={vi.fn()} roomId="room-1" onSlashForm={vi.fn(() => null)}
        slashDescription={name => (name === 'routine' ? 'drafted by Builder' : null)}
      />,
    )
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/ro', selectionStart: 3 } })
    expect(screen.getByText(/drafted by Builder/)).toBeInTheDocument()
  })

  it('does not offer /routine or /goal where no form can open', () => {
    render(<MessageInput onSend={vi.fn()} onTyping={vi.fn()} roomId="room-1" />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/', selectionStart: 1 } })
    expect(screen.getByText('/task @agent title')).toBeInTheDocument()
    expect(screen.queryByText('/goal description')).not.toBeInTheDocument()
  })

  it('[MSG-10] opens the routine form with the parsed assignee and title instead of sending', () => {
    const onSend = vi.fn()
    const onSlashForm = vi.fn(() => null)
    render(<MessageInput onSend={onSend} onTyping={vi.fn()} roomId="room-1" onSlashForm={onSlashForm} />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/routine <@user:p1> daily report', selectionStart: 33 } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSlashForm).toHaveBeenCalledWith({ command: 'routine', payload: { assignee_pid: 'p1', text: 'daily report' } })
    expect(onSend).not.toHaveBeenCalled()
    expect(input).toHaveValue('')
  })

  it('[MSG-10] keeps the text and shows the reason when the form cannot open', () => {
    const onSlashForm = vi.fn(() => 'Goals need a project room with a lead agent.')
    render(<MessageInput onSend={vi.fn()} onTyping={vi.fn()} roomId="room-1" onSlashForm={onSlashForm} />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/goal faster parser', selectionStart: 19 } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSlashForm).toHaveBeenCalledWith({ command: 'goal', payload: { text: 'faster parser' } })
    expect(screen.getByTestId('slash-error')).toHaveTextContent('Goals need a project room with a lead agent.')
    expect(input).toHaveValue('/goal faster parser')
  })

  it('sends /goal as a plain message where the command is unavailable', () => {
    const onSend = vi.fn()
    render(<MessageInput onSend={onSend} onTyping={vi.fn()} roomId="room-1" />)
    const input = screen.getByPlaceholderText(placeholder)
    fireEvent.change(input, { target: { value: '/goal faster parser', selectionStart: 19 } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('/goal faster parser', undefined)
  })
})
