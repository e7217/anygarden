// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { PackageUpdate } from '@/hooks/useSystemVersion'

const state = vi.hoisted(() => ({ updates: [] as PackageUpdate[], machines: [] as unknown[] }))
vi.mock('@/hooks/useSystemVersion', () => ({
  useUpdateStatus: () => ({ updates: state.updates, loading: false, error: null, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useMachines', () => ({ useMachines: () => ({ machines: state.machines, status: 'loaded', fetchMachines: vi.fn().mockResolvedValue(undefined) }) }))
vi.mock('@/hooks/useAgents', () => ({ useAgents: () => ({ agents: [], availableEngines: [], pendingIds: new Set() }) }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ projects: [], rooms: {}, fetchAgentDMs: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async () => ({ ok: true, json: async () => ({ agents: [], engines: [], activity: [] }) })) }))
vi.mock('@/components/CreateAgentDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsMenu', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))
import AdminMachines from './AdminMachines'

afterEach(() => { cleanup(); state.updates = []; state.machines = [] })

const serverRow = (update_available: boolean): PackageUpdate => ({
  package: 'anygarden',
  current: '0.21.0',
  latest: update_available ? '0.22.0' : '0.21.0',
  update_available,
  checked_at: '2026-10-01T00:00:00Z',
  error: null,
})

it('[#888] lists this server above the machines even when no machine is registered', () => {
  state.updates = [serverRow(false)]
  render(<AdminMachines />)
  const entry = screen.getByRole('button', { name: /This server/ })
  expect(entry).toHaveTextContent('v0.21.0')
  expect(screen.getByText('No machines registered')).toBeInTheDocument()
})

it('[#888] shows the server version and update command when the server entry is selected', () => {
  state.updates = [serverRow(true)]
  render(<AdminMachines />)
  const entry = screen.getByRole('button', { name: /This server/ })
  expect(entry).toHaveTextContent('update available')
  fireEvent.click(entry)
  expect(entry).toHaveAttribute('aria-current', 'true')
  const panel = screen.getByRole('region', { name: 'This server' })
  expect(within(panel).getByText('pip install -U anygarden')).toBeInTheDocument()
  expect(within(panel).getByRole('button', { name: 'Check for updates' })).toBeInTheDocument()
})
