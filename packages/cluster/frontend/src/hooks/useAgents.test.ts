// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useAgents, type Agent } from './useAgents'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('preserves a committed creation when refreshing the agent list fails', async () => {
  const created: Agent = {
    id: 'new-agent', name: 'Reviewer', engine: 'codex-cli', restart_policy: 'restart_anywhere',
    actual_state: 'running', desired_state: 'running', placed_on_machine_id: 'machine',
  }
  let posted = false
  mocks.apiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/api/v1/agents' && init?.method === 'POST') {
      posted = true
      return new Response(JSON.stringify(created), { status: 201 })
    }
    if (path === '/api/v1/agents' && posted) throw new Error('List refresh unavailable')
    return new Response('[]', { status: 200 })
  })
  const { result } = renderHook(useAgents)
  await act(async () => { await Promise.resolve() })
  let response: Agent | undefined
  await act(async () => {
    response = await result.current.createAgent({ name: created.name, engine: created.engine, machine_id: 'machine' })
  })
  expect(response).toEqual(created)
  expect(result.current.agents).toEqual([created])
  expect(mocks.apiFetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

it('keeps the successful permission response when refresh fails and a prior GET arrives late', async () => {
  const old = { id: 'agent', permission_level: 'standard', actual_state: 'running' } as Agent
  const updated = { ...old, permission_level: 'restricted' } as Agent
  let resolveOld!: (response: Response) => void
  let listCalls = 0
  mocks.apiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return new Response(JSON.stringify(updated), { status: 200 })
    if (path !== '/api/v1/agents') return new Response('[]')
    listCalls += 1
    if (listCalls === 1) return new Response(JSON.stringify([old]))
    if (listCalls === 2) return new Promise<Response>(resolve => { resolveOld = resolve })
    throw new Error('Refresh failed')
  })
  const { result } = renderHook(useAgents)
  await act(async () => { await Promise.resolve() })
  const stale = result.current.fetchAgents()
  let response: Agent | undefined
  await act(async () => {
    response = await result.current.updateAgent('agent', { permission_level: 'restricted', permission_level_set: true })
  })
  expect(response).toEqual(updated)
  expect(result.current.agents).toEqual([updated])
  await act(async () => { resolveOld(new Response(JSON.stringify([old]))); await stale })
  expect(result.current.agents).toEqual([updated])
})

it('returns a successful update without waiting for a slow reconciliation request', async () => {
  const old = { id: 'agent', permission_level: 'standard', actual_state: 'running' } as Agent
  const updated = { ...old, permission_level: 'restricted' } as Agent
  let resolveRefresh!: (response: Response) => void
  let listCalls = 0
  mocks.apiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return new Response(JSON.stringify(updated))
    if (path !== '/api/v1/agents') return new Response('[]')
    if (++listCalls === 1) return new Response(JSON.stringify([old]))
    return new Promise<Response>(resolve => { resolveRefresh = resolve })
  })
  const { result } = renderHook(useAgents)
  await act(async () => { await Promise.resolve() })
  let response: Agent | undefined
  await act(async () => {
    response = await result.current.updateAgent('agent', { permission_level: 'restricted', permission_level_set: true })
  })
  expect(response).toEqual(updated)
  expect(result.current.agents).toEqual([updated])
  await act(async () => { resolveRefresh(new Response(JSON.stringify([updated]))); await Promise.resolve() })
})
