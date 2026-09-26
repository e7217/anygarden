// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import CreateAgentDialog from './CreateAgentDialog'
import type { Agent } from '@/hooks/useAgents'

const mocks = vi.hoisted(() => ({ discover: vi.fn() }))
vi.mock('@/lib/engineEndpoints', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/engineEndpoints')>(),
  discoverEndpointModels: mocks.discover,
}))

afterEach(cleanup)

function setup(overrides: Partial<React.ComponentProps<typeof CreateAgentDialog>> = {}) {
  const agent = { id: 'new-agent', name: 'Reviewer', engine: 'codex-cli' } as Agent
  const props: React.ComponentProps<typeof CreateAgentDialog> = {
    open: true, onOpenChange: vi.fn(), machineId: 'chosen-machine', machineName: 'Studio',
    engines: [{ engine: 'codex-cli' }], availableEngines: [],
    projects: [{ id: 'project', name: 'Development' }],
    roomsByProject: { project: [
      { id: 'channel', name: 'Engineering', project_id: 'project', is_dm: false },
      { id: 'room', name: 'Review', project_id: 'project', parent_room_id: 'channel', is_dm: false },
      { id: 'dm', name: 'Private agent', project_id: null, is_dm: true },
    ] },
    createAgent: vi.fn().mockResolvedValue(agent),
    fetchEngineCatalog: vi.fn().mockResolvedValue(null), onCreated: vi.fn(),
    ...overrides,
  }
  render(<CreateAgentDialog {...props} />)
  return { props, agent }
}

it('sends role, permission, room membership, and the selected machine together', async () => {
  const { props, agent } = setup()
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewer' } })
  fireEvent.change(screen.getByLabelText('Agent description'), { target: { value: 'Reviews pull requests' } })
  fireEvent.change(screen.getByLabelText('Agent permission tier'), { target: { value: 'restricted' } })
  fireEvent.click(screen.getByLabelText('# Engineering'))
  fireEvent.click(screen.getByLabelText('↳ Review'))
  expect(screen.queryByLabelText('Private agent')).toBeNull()
  fireEvent.click(screen.getByText('Add role instructions (optional)'))
  fireEvent.change(screen.getByLabelText('AGENTS.md'), { target: { value: 'Review before making edits.' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(props.createAgent).toHaveBeenCalledWith({
    name: 'Reviewer', description: 'Reviews pull requests', engine: 'codex-cli',
    request_id: expect.any(String),
    machine_id: 'chosen-machine', permission_level: 'restricted',
    agents_md: 'Review before making edits.', rooms: ['channel', 'room'],
  }))
  expect(props.onCreated).toHaveBeenCalledWith(agent)
  expect(props.onOpenChange).toHaveBeenCalledWith(false)
})

it('does not report an empty room list while loading', () => {
  setup({ projects: [], roomsByProject: {}, roomsStatus: 'loading' })
  expect(screen.getByRole('status')).toHaveTextContent('Loading')
  expect(screen.queryByText(/No project rooms are available/)).toBeNull()
})

it('offers a retry when shared rooms cannot be loaded', () => {
  const onRetryRooms = vi.fn().mockResolvedValue(undefined)
  setup({ projects: [], roomsByProject: {}, roomsStatus: 'error', onRetryRooms })
  expect(screen.getByRole('alert')).toHaveTextContent('Rooms could not be loaded')
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(onRetryRooms).toHaveBeenCalledOnce()
})

it('retains the completed form on a rejected creation so it can be corrected', async () => {
  const createAgent = vi.fn().mockRejectedValue(new Error('Selected machine must be online and connected'))
  const { props } = setup({ createAgent })
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewer' } })
  fireEvent.change(screen.getByLabelText('Agent description'), { target: { value: 'Reviews changes' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Selected machine must be online and connected')
  expect(screen.getByLabelText('Agent description')).toHaveValue('Reviews changes')
  expect(screen.getByRole('button', { name: 'Create Agent' })).toBeEnabled()
  expect(props.onCreated).not.toHaveBeenCalled()
})

it('reuses the same request id after an uncertain create response, including edited retries', async () => {
  const createAgent = vi.fn().mockRejectedValue(new Error('Network error'))
  setup({ createAgent })
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewer' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await screen.findByRole('alert')
  const key = createAgent.mock.calls[0][0].request_id
  expect(key).toMatch(/^[0-9a-f-]{36}$/)
  fireEvent.change(screen.getByLabelText('Agent description'), { target: { value: 'Changed after timeout' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(createAgent).toHaveBeenCalledTimes(2))
  expect(createAgent.mock.calls[1][0].request_id).toBe(key)
})

const catalog = {
  engine: 'codex-cli', default_model: 'gpt-6-sol', reasoning_levels: ['high', 'ultra'],
  models: [
    { id: 'gpt-6-sol', label: 'Sol', reasoning_levels: ['high', 'ultra'] },
    { id: 'gpt-6-luna', label: 'Luna', reasoning_levels: ['high'] },
  ],
}

it.each(['ultra', 'high'])('keeps only reasoning supported after switching models: %s', async effort => {
  const { props } = setup({ fetchEngineCatalog: vi.fn().mockResolvedValue(catalog) })
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Reviewer' } })
  fireEvent.change(await screen.findByLabelText('Reasoning Effort'), { target: { value: effort } })
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gpt-6-luna' } })
  expect(screen.getByLabelText('Reasoning Effort')).toHaveValue(effort === 'high' ? 'high' : '')
  fireEvent.click(screen.getByRole('button', { name: 'Create Agent' }))
  await waitFor(() => expect(props.createAgent).toHaveBeenCalledOnce())
  expect(vi.mocked(props.createAgent).mock.calls[0][0].reasoning_effort).toBe(effort === 'high' ? 'high' : undefined)
})

it('ignores a direct model lookup that finishes after switching back to native Pi', async () => {
  let resolve!: (value: { models: { id: string }[]; reachable_from: string }) => void
  mocks.discover.mockReturnValue(new Promise(done => { resolve = done }))
  setup({ engines: [{ engine: 'pi-cli' }] })
  fireEvent.change(screen.getByLabelText('Connection type'), { target: { value: 'direct' } })
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://localhost:8000/v1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }))
  fireEvent.change(screen.getByLabelText('Connection type'), { target: { value: 'native' } })
  await act(async () => { resolve({ models: [{ id: 'stale-model' }], reachable_from: 'server' }) })
  expect(screen.getByLabelText('Model (required)')).toHaveValue('')
})

it('ignores an old endpoint lookup after its URL changes', async () => {
  let resolve!: (value: { models: { id: string }[]; reachable_from: string }) => void
  mocks.discover.mockReturnValue(new Promise(done => { resolve = done }))
  setup({ engines: [{ engine: 'pi-cli' }] })
  fireEvent.change(screen.getByLabelText('Connection type'), { target: { value: 'direct' } })
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://old-server/v1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load models' }))
  fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'http://new-server/v1' } })
  await act(async () => { resolve({ models: [{ id: 'old-server-model' }], reachable_from: 'server' }) })
  expect(screen.getByLabelText('Model (required)')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Load models' })).toBeEnabled()
  expect(screen.queryByRole('status')).toBeNull()
})
