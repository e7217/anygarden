// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useRoomTasks } from './useRoomTasks'
import { useRoomGoals } from './useRoomGoals'
import { useAgentGoals } from './useAgentGoals'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const response = (id: string, status = 200) => new Response(JSON.stringify([{ id }]), { status })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

for (const kind of ['tasks', 'goals', 'agent goals'] as const) {
  const useList = kind === 'tasks' ? useRoomTasks : kind === 'goals' ? useRoomGoals : useAgentGoals
  const rows = (value: ReturnType<typeof useList>) => 'tasks' in value ? value.tasks : value.goals
  describe(`${kind} scope isolation`, () => {
    it('hides previous rows immediately and ignores A responses after A → B → A', async () => {
      const oldA = deferred<Response>()
      const newA = deferred<Response>()
      vi.spyOn(globalThis, 'fetch')
        .mockReturnValueOnce(oldA.promise)
        .mockResolvedValueOnce(response('B'))
        .mockReturnValueOnce(newA.promise)
      const { result, rerender } = renderHook(({ room }) => useList(room), { initialProps: { room: 'A' } })
      rerender({ room: 'B' })
      await waitFor(() => expect(rows(result.current)[0]?.id).toBe('B'))
      rerender({ room: 'A' })
      expect(rows(result.current)).toEqual([])
      await act(async () => { oldA.resolve(response('stale A')) })
      expect(rows(result.current)).toEqual([])
      await act(async () => { newA.resolve(response('current A')) })
      expect(rows(result.current)[0]?.id).toBe('current A')
    })

    it('keeps the newest refresh when the earlier JSON body arrives last', async () => {
      const body = deferred<unknown>()
      vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce({ ok: true, json: () => body.promise } as Response)
        .mockResolvedValueOnce(response('latest'))
      const { result } = renderHook(() => useList('A'))
      await act(async () => { await Promise.resolve() })
      await act(async () => { await result.current.refresh() })
      await act(async () => { body.resolve([{ id: 'old' }]) })
      expect(rows(result.current)[0]?.id).toBe('latest')
    })

    it.each([204, 500])('does not refresh or publish a stale mutation result (HTTP %s)', async status => {
      const mutation = deferred<Response>()
      const fetch = vi.spyOn(globalThis, 'fetch')
        .mockResolvedValueOnce(response('A'))
        .mockReturnValueOnce(mutation.promise)
        .mockResolvedValueOnce(response('B'))
      const { result, rerender } = renderHook(({ room }) => useList(room), { initialProps: { room: 'A' } })
      await waitFor(() => expect(rows(result.current)[0]?.id).toBe('A'))
      let pending!: Promise<void>
      act(() => { pending = result.current.remove('A') })
      const oldRemove = result.current.remove
      rerender({ room: 'B' })
      await waitFor(() => expect(rows(result.current)[0]?.id).toBe('B'))
      await act(async () => { mutation.resolve(new Response(null, { status })); await pending })
      await act(async () => { await oldRemove('A') })
      expect(fetch).toHaveBeenCalledTimes(3)
      expect(rows(result.current)[0]?.id).toBe('B')
      expect(result.current.error).toBeNull()
    })

    it('drops a pending response when suspended', async () => {
      const pending = deferred<Response>()
      vi.spyOn(globalThis, 'fetch').mockReturnValue(pending.promise)
      const { result, rerender } = renderHook(({ room }: { room: string | null }) => useList(room), { initialProps: { room: 'A' as string | null } })
      rerender({ room: null })
      await act(async () => { pending.resolve(response('A')) })
      expect(rows(result.current)).toEqual([])
      expect(result.current.loading).toBe(false)
    })
  })
}
