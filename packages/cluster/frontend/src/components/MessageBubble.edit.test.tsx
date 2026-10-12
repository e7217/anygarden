// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MessageBubble from './MessageBubble'
import type { ChatMessage } from '@/hooks/useWebSocket'
import type { Participant } from '@/pages/ChatPage'
import { MESSAGE_UPDATED_EVENT } from '@/lib/messageEdits'

const apiFetch = vi.fn()
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ rooms: {} }) }))
vi.mock('@/components/EntityAvatar', () => ({
  EntityAvatar: ({ name }: { name: string }) => <span>{name.slice(0, 2)}</span>,
}))
const confirm = vi.fn(async () => true)
const notify = vi.fn()
vi.mock('@/components/feedback/FeedbackProvider', () => ({
  useFeedback: () => ({ confirm, notify }),
}))

const participants: Record<string, Participant> = {
  me: { id: 'me', display_name: 'Me', kind: 'user' },
  bot: { id: 'bot', display_name: 'Bot', kind: 'agent', engine: 'codex' },
}

function msg(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    type: 'message', id: 'm1', room_id: 'r1', participant_id: 'me',
    content: 'original text', seq: 7, created_at: new Date().toISOString(),
    ...overrides,
  }
}

function okJson(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => { apiFetch.mockReset(); confirm.mockClear(); notify.mockClear() })
afterEach(() => cleanup())

describe('MessageBubble edit/delete', () => {
  it('[MSG-08] shows edit and delete actions only on my own plain messages', () => {
    const { rerender } = render(<MessageBubble message={msg()} participants={participants} isMine />)
    expect(screen.getByRole('button', { name: 'Edit message' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete message' })).toBeInTheDocument()

    rerender(<MessageBubble message={msg({ participant_id: 'bot' })} participants={participants} isMine={false} />)
    expect(screen.queryByRole('button', { name: 'Edit message' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete message' })).toBeNull()

    rerender(<MessageBubble message={msg({ metadata: { system_origin: 'x' } })} participants={participants} isMine />)
    expect(screen.queryByRole('button', { name: 'Edit message' })).toBeNull()
  })

  it('[MSG-08] saves an edit through PATCH and publishes the updated row', async () => {
    const updated = msg({ content: 'new text', metadata: { edited_at: '2026-10-12T01:00:00Z' } })
    apiFetch.mockResolvedValue(okJson(updated))
    const seen: ChatMessage[] = []
    const listener = (e: Event) => seen.push((e as CustomEvent<ChatMessage>).detail)
    window.addEventListener(MESSAGE_UPDATED_EVENT, listener)

    render(<MessageBubble message={msg()} participants={participants} isMine />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }))
    const box = screen.getByRole('textbox', { name: 'Edit message' })
    expect(box).toHaveValue('original text')
    fireEvent.change(box, { target: { value: 'new text' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })

    expect(apiFetch).toHaveBeenCalledWith('/api/v1/rooms/r1/messages/m1', {
      method: 'PATCH', body: JSON.stringify({ content: 'new text' }),
    })
    expect(seen).toEqual([updated])
    expect(screen.queryByRole('textbox', { name: 'Edit message' })).toBeNull()
    window.removeEventListener(MESSAGE_UPDATED_EVENT, listener)
  })

  it('[MSG-08] Escape cancels editing without a request', () => {
    render(<MessageBubble message={msg()} participants={participants} isMine />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }))
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Edit message' }), { key: 'Escape' })
    expect(screen.queryByRole('textbox', { name: 'Edit message' })).toBeNull()
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('[MSG-08] confirms before deleting and calls DELETE', async () => {
    apiFetch.mockResolvedValue(okJson(msg({ content: '', metadata: { deleted_at: 't' } })))
    render(<MessageBubble message={msg()} participants={participants} isMine />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete message' })) })
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/rooms/r1/messages/m1', { method: 'DELETE' })
  })

  it('[MSG-08] does not delete when the confirmation is declined', async () => {
    confirm.mockResolvedValueOnce(false)
    render(<MessageBubble message={msg()} participants={participants} isMine />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete message' })) })
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('[MSG-08] shows an error notice when the server refuses the change', async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify({ detail: 'nope' }), { status: 403 }))
    render(<MessageBubble message={msg()} participants={participants} isMine />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Edit message' }), { target: { value: 'x' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save' })) })
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ tone: 'error' }))
    expect(screen.getByRole('textbox', { name: 'Edit message' })).toBeInTheDocument()
  })

  it('[MSG-08] renders the (edited) marker', () => {
    render(<MessageBubble message={msg({ metadata: { edited_at: 't' } })} participants={participants} isMine />)
    expect(screen.getByText('(edited)')).toBeInTheDocument()
  })

  it('[MSG-08] renders a deleted placeholder without actions, for me and for others', () => {
    const deleted = msg({ content: '', metadata: { deleted_at: 't' } })
    const { rerender } = render(<MessageBubble message={deleted} participants={participants} isMine />)
    expect(screen.getByText('This message was deleted')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit message' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Delete message' })).toBeNull()
    rerender(<MessageBubble message={deleted} participants={participants} isMine={false} />)
    expect(screen.getByText('This message was deleted')).toBeInTheDocument()
  })
})
