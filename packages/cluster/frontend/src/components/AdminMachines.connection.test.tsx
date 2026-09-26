// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({ registerMachine: vi.fn(), fetchMachines: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/hooks/useMachines', () => ({ useMachines: () => ({ machines: [], status: 'loaded', registerMachine: mocks.registerMachine, fetchMachines: mocks.fetchMachines }) }))
vi.mock('@/hooks/useAgents', () => ({ useAgents: () => ({ agents: [], availableEngines: [], pendingIds: new Set() }) }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ projects: [], rooms: {}, fetchAgentDMs: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async () => ({ ok: true, json: async () => [] })) }))
vi.mock('@/components/CreateAgentDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsMenu', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))
import AdminMachines from './AdminMachines'
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('continues registration into the connection guide even when the machine list refresh failed', async () => {
  mocks.registerMachine.mockResolvedValue({ id: 'new-machine', name: 'Office host', hostname: '', machine_token: 'one-time-token', refreshWarning: 'Machine created, list could not refresh' })
  render(<AdminMachines />)
  fireEvent.click(screen.getByRole('button', { name: 'Register machine' }))
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: ' Office host ' } })
  fireEvent.change(screen.getByLabelText('Description (optional)'), { target: { value: 'Remote worker' } })
  fireEvent.click(screen.getByRole('button', { name: /^Register$/ }))
  expect(await screen.findByRole('dialog', { name: 'Connect Office host' })).toBeInTheDocument()
  expect(mocks.registerMachine).toHaveBeenCalledWith({ name: 'Office host', description: 'Remote worker' })
  expect(screen.getByText('one-time-token')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Machine created, list could not refresh')
  fireEvent.click(screen.getByRole('button', { name: 'Check connection' }))
  await waitFor(() => expect(mocks.fetchMachines).toHaveBeenCalledOnce())
})

it('keeps the registration draft and surfaces a failed request', async () => {
  mocks.registerMachine.mockRejectedValue(new Error('server unavailable'))
  render(<AdminMachines />)
  fireEvent.click(screen.getByRole('button', { name: 'Register machine' }))
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'My machine' } })
  fireEvent.click(screen.getByRole('button', { name: /^Register$/ }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not register the machine')
  expect(screen.getByLabelText('Name')).toHaveValue('My machine')
  expect(screen.getByRole('button', { name: /^Register$/ })).toBeEnabled()
})
