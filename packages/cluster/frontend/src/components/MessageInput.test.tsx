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
