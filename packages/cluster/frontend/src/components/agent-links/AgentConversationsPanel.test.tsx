// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import AgentConversationsPanel from './AgentConversationsPanel'
const mocks = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ createAgentDM: mocks.create }) }))
vi.mock('@/hooks/useAgentConversations', () => ({ useAgentConversations: () => ({ rooms: [{ id: 'chosen', name: 'Alpha' }, { id: 'other', name: 'Beta' }], loading: false, errorStatus: null, refresh: vi.fn() }) }))
function Destination() { return <p>{useLocation().pathname}</p> }
function setup(admin = true) {
  render(<MemoryRouter><Routes><Route path="/" element={<AgentConversationsPanel agentId="a" user={{ id: 'u', is_admin: admin }} />} /><Route path="/rooms/:id" element={<Destination />} /></Routes></MemoryRouter>)
}
afterEach(() => { cleanup(); vi.resetAllMocks() })
it('[COL-18] opens precisely the chosen conversation without creating a room', () => {
  setup(false)
  expect(screen.queryByRole('button', { name: 'New conversation' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('link', { name: 'Beta' }))
  expect(screen.getByText('/rooms/other')).toBeInTheDocument()
  expect(mocks.create).not.toHaveBeenCalled()
})
it('[COL-18] prevents double creation while a request is pending', async () => {
  let finish!: (room: { id: string }) => void
  mocks.create.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  setup()
  const button = screen.getByRole('button', { name: 'New conversation' })
  fireEvent.click(button)
  fireEvent.click(button)
  expect(mocks.create).toHaveBeenCalledTimes(1)
  await act(async () => { finish({ id: 'new' }) })
  await waitFor(() => expect(screen.getByText('/rooms/new')).toBeInTheDocument())
})
