// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { RoomsProvider, useRooms } from './useRooms'

const apiFetch = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({ apiFetch }))

const PROJECTS = [
  { id: 'p0', name: 'Zero' },
  { id: 'p1', name: 'One' },
  { id: 'p2', name: 'Two' },
]

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

function wrapper({ children }: { children: ReactNode }) {
  return <RoomsProvider>{children}</RoomsProvider>
}

async function renderLoaded() {
  const hook = renderHook(() => useRooms(), { wrapper })
  await act(async () => { await hook.result.current.fetchProjects() })
  await waitFor(() => expect(hook.result.current.projects).toHaveLength(3))
  return hook
}

const ids = (projects: Array<{ id: string }>) => projects.map(p => p.id)

beforeEach(() => {
  apiFetch.mockReset()
  apiFetch.mockImplementation(async (url: string) =>
    url === '/api/v1/projects' ? json(PROJECTS) : json([]),
  )
})

afterEach(() => { vi.restoreAllMocks() })

describe('useRooms — project order (#995)', () => {
  it('[ROOM-14] reorders projects at once and saves the full order', async () => {
    const { result } = await renderLoaded()

    await act(async () => { await result.current.reorderProjects(['p2', 'p0', 'p1']) })

    expect(ids(result.current.projects)).toEqual(['p2', 'p0', 'p1'])
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/projects/order', {
      method: 'PUT',
      body: JSON.stringify({ project_ids: ['p2', 'p0', 'p1'] }),
    })
  })

  it('restores the previous order when saving fails', async () => {
    const { result } = await renderLoaded()
    apiFetch.mockImplementation(async () => json({}, 500))

    await act(async () => {
      await expect(result.current.reorderProjects(['p2', 'p0', 'p1'])).rejects.toThrow()
    })

    expect(ids(result.current.projects)).toEqual(['p0', 'p1', 'p2'])
  })

  it('[ROOM-14] applies an order pushed from another session', async () => {
    const { result } = await renderLoaded()

    act(() => {
      window.dispatchEvent(new CustomEvent('anygarden:projects:order', {
        detail: { project_ids: ['p1', 'p2'] },
      }))
    })

    // Projects missing from the pushed order keep their place after it.
    expect(ids(result.current.projects)).toEqual(['p1', 'p2', 'p0'])
  })

  it('does not refetch every project\'s rooms after a reorder', async () => {
    const { result } = await renderLoaded()
    await waitFor(() =>
      expect(apiFetch.mock.calls.filter(([u]) => String(u).startsWith('/api/v1/rooms?project_id=')).length).toBeGreaterThanOrEqual(3),
    )
    apiFetch.mockClear()

    await act(async () => { await result.current.reorderProjects(['p2', 'p0', 'p1']) })

    expect(apiFetch.mock.calls.map(([u]) => u)).toEqual(['/api/v1/projects/order'])
  })
})
