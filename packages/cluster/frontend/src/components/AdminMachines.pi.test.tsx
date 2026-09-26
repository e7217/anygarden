// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  createAgent: vi.fn().mockResolvedValue({}),
  fetchAgentDMs: vi.fn(),
  fetchEngineCatalog: vi.fn().mockResolvedValue({
    engine: 'pi-cli', default_model: '', reasoning_levels: [],
    models: [{ id: 'glm-5.3-flash', label: 'GLM 5.3 Flash (zai)', reasoning_levels: [] }],
    supported_versions: ['0.85.1'],
  }),
  machines: [{ id: 'm1', name: 'Test machine', hostname: 'test', status: 'online' }],
  agents: [], availableEngines: [{ engine: 'pi-cli', machine_count: 1 }],
  piVersion: '0.85.1',
}))
vi.mock('@/hooks/useMachines', () => ({ useMachines: () => ({ machines: mocks.machines }) }))
vi.mock('@/hooks/useAgents', () => ({ useAgents: () => ({
  createAgent: mocks.createAgent, fetchEngineCatalog: mocks.fetchEngineCatalog,
  agents: mocks.agents, availableEngines: mocks.availableEngines, pendingIds: new Set(),
}) }))
vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ projects: [], rooms: {}, fetchAgentDMs: mocks.fetchAgentDMs }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async (path: string) => ({
  ok: true, json: async () => path.endsWith('/engines') ? [{ engine: 'pi-cli', version: mocks.piVersion }] : [],
})) }))
vi.mock('@/components/AgentSettingsDialog', () => ({ default: () => null }))
vi.mock('@/components/AgentSettingsMenu', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))

import AdminMachines from './AdminMachines'
afterEach(() => { cleanup(); vi.clearAllMocks(); mocks.piVersion = '0.85.1' })

async function selectPiEngine() {
  await waitFor(() => expect(document.querySelector('option[value="pi-cli"]')).not.toBeNull())
  fireEvent.change(screen.getByLabelText('Engine'), { target: { value: 'pi-cli' } })
}

it('requires an explicit provider and submits custom provider/model from Pi creation', async () => {
  render(<AdminMachines />)
  fireEvent.click(await screen.findByRole('button', { name: 'New Agent' }))
  await selectPiEngine()
  const provider = await screen.findByLabelText('Provider (required)')
  expect(screen.getByText(/save a Pi provider API key before starting/)).toBeInTheDocument()
  fireEvent.change(screen.getByPlaceholderText('Agent name'), { target: { value: 'Local worker' } })
  const submit = screen.getByRole('button', { name: 'Create Agent' })
  expect(provider).toHaveValue('')
  expect(submit).toBeDisabled()
  fireEvent.change(provider, { target: { value: '--help' } })
  expect(submit).toBeDisabled()
  fireEvent.change(provider, { target: { value: 'my-local' } })
  fireEvent.change(screen.getByLabelText('Model (required)'), { target: { value: 'local-model-v2' } })
  expect(submit).toBeEnabled()
  fireEvent.click(submit)
  await waitFor(() => expect(mocks.createAgent).toHaveBeenCalledWith({
    name: 'Local worker', engine: 'pi-cli', provider: 'my-local', model: 'local-model-v2', rooms: [],
  }))
})

it('warns before creation when the machine Pi version fails the adapter gate (#687)', async () => {
  mocks.piVersion = '0.87.1'
  render(<AdminMachines />)
  fireEvent.click(await screen.findByRole('button', { name: 'New Agent' }))
  await selectPiEngine()
  await screen.findByLabelText('Provider (required)')
  expect(await screen.findByText(/has pi-cli 0\.87\.1, but this build requires 0\.85\.1/)).toBeInTheDocument()
})

it('shows no version warning for the supported Pi version', async () => {
  render(<AdminMachines />)
  fireEvent.click(await screen.findByRole('button', { name: 'New Agent' }))
  await selectPiEngine()
  await screen.findByLabelText('Provider (required)')
  expect(screen.queryByText(/but this build requires/)).not.toBeInTheDocument()
})
