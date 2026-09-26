// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import { useRoomParticipants } from './useRoomParticipants'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const agent = { id: 'a', display_name: 'Reviewer', kind: 'agent', agent_id: 'agent-a', role: 'admin',
  description: 'Reviews changes', engine: 'codex-cli', avatar_kind: 'emoji', avatar_value: '🔎', online: false }
function response(participants = [agent], status = 200) {
  return { ok: status === 200, status, json: async () => ({ name: 'Team', participants }) } as Response
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
function roster(participants: unknown[], room = 'r') {
  window.dispatchEvent(new CustomEvent('anygarden:rooms:settings-changed', { detail: { room_id: room, participants } }))
}
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('preserves public description and the complete REST identity', async () => {
  fetchMock.mockResolvedValue(response())
  const { result } = renderHook(() => useRoomParticipants('r'))
  await waitFor(() => expect(result.current.participants.a?.description).toBe('Reviews changes'))
  expect(result.current.participants.a).toMatchObject(agent)
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/rooms/r', expect.anything())
})

it('applies a slim roster without losing roles, avatars or presence when refresh fails', async () => {
  fetchMock.mockResolvedValueOnce(response()).mockResolvedValue(response([], 503))
  const { result } = renderHook(() => useRoomParticipants('r'))
  await waitFor(() => expect(result.current.participants.a).toBeDefined())
  act(() => roster([{ id: 'a', display_name: 'Renamed', kind: 'agent', agent_id: 'agent-a', description: null }]))
  await waitFor(() => expect(result.current.errorStatus).toBe(503))
  expect(result.current.participants.a).toMatchObject({ ...agent, display_name: 'Renamed', description: null })
})

it('ignores old-room responses even after their JSON decoding finishes late', async () => {
  const body = deferred<unknown>()
  fetchMock.mockResolvedValueOnce({ ok: true, json: () => body.promise } as Response)
    .mockResolvedValueOnce(response([{ ...agent, id: 'b', description: 'B' }]))
  const { result, rerender } = renderHook(({ id }) => useRoomParticipants(id), { initialProps: { id: 'a-room' } })
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  rerender({ id: 'b-room' })
  expect(result.current.participants).toEqual({})
  await waitFor(() => expect(result.current.participants.b?.description).toBe('B'))
  await act(async () => { body.resolve({ participants: [agent] }) })
  expect(result.current.participants.a).toBeUndefined()
})

it('invalidates a pending REST snapshot when a newer roster arrives', async () => {
  const oldBody = deferred<unknown>()
  fetchMock.mockResolvedValueOnce({ ok: true, json: () => oldBody.promise } as Response)
    .mockResolvedValue(response([{ ...agent, description: 'New role' }]))
  const { result } = renderHook(() => useRoomParticipants('r'))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  act(() => roster([{ id: 'a', kind: 'agent', display_name: 'Reviewer', description: 'New role' }]))
  await waitFor(() => expect(result.current.loading).toBe(false))
  await act(async () => { oldBody.resolve({ participants: [agent] }) })
  expect(result.current.participants.a.description).toBe('New role')
})

it('keeps presence updates received during a roster hydration request', async () => {
  const body = deferred<unknown>()
  fetchMock.mockResolvedValueOnce(response()).mockResolvedValueOnce({ ok: true, json: () => body.promise } as Response)
  const { result } = renderHook(() => useRoomParticipants('r'))
  await waitFor(() => expect(result.current.participants.a).toBeDefined())
  act(() => roster([{ id: 'a', kind: 'agent', display_name: 'Reviewer', description: 'New role' }]))
  act(() => window.dispatchEvent(new CustomEvent('anygarden:presence:update', {
    detail: { room_id: 'r', participant_id: 'a', online: true, last_seen_at: 'now' },
  })))
  await act(async () => { body.resolve({ participants: [{ ...agent, description: 'New role' }] }) })
  expect(result.current.participants.a).toMatchObject({ online: true, last_seen_at: 'now', description: 'New role' })
})

it('removes departed members, hydrates new members, and ignores another room', async () => {
  const newcomer = { ...agent, id: 'b', agent_id: 'agent-b', description: 'New member' }
  fetchMock.mockResolvedValueOnce(response()).mockResolvedValue(response([newcomer]))
  const { result } = renderHook(() => useRoomParticipants('r'))
  await waitFor(() => expect(result.current.participants.a).toBeDefined())
  act(() => roster([], 'elsewhere'))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  act(() => roster([{ id: 'b', kind: 'agent', display_name: 'B', description: 'New member' }]))
  expect(result.current.participants.a).toBeUndefined()
  await waitFor(() => expect(result.current.participants.b?.engine).toBe('codex-cli'))
  expect(result.current.participants.b.agent_id).toBe('agent-b')
})

it('does not fetch when no authorized room is selected or after unmount', async () => {
  const { result, unmount } = renderHook(() => useRoomParticipants(null))
  expect(fetchMock).not.toHaveBeenCalled()
  unmount()
  await result.current.refresh()
  expect(fetchMock).not.toHaveBeenCalled()
})
