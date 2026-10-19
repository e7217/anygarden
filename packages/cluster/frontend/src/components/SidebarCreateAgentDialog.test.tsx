// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import SidebarCreateAgentDialog from './SidebarCreateAgentDialog'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  projects: [] as Array<{ id: string; name: string }>,
  rooms: {} as Record<string, unknown[]>,
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({ projects: mocks.projects, rooms: mocks.rooms, status: 'ready', refetch: vi.fn() }),
}))

const json = (body: unknown) => Promise.resolve({ ok: true, json: async () => body })
function serve(machines: Array<{ id: string; name: string; status: string }>) {
  mocks.apiFetch.mockImplementation((url: unknown) => {
    if (typeof url !== 'string') return json([])
    if (url === '/api/v1/machines') return json(machines)
    if (url.endsWith('/engines')) return json([{ engine: 'codex-cli' }])
    return json([])
  })
}

function renderDialog(initialRoomIds?: string[]) {
  const onOpenChange = vi.fn()
  render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<SidebarCreateAgentDialog open onOpenChange={onOpenChange}
          createAgent={vi.fn()} fetchEngineCatalog={vi.fn().mockResolvedValue(null)} availableEngines={[]} onCreated={vi.fn()} initialRoomIds={initialRoomIds} />} />
        <Route path="/admin/machines" element={<p>machines page</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return { onOpenChange }
}

beforeEach(() => { mocks.apiFetch.mockReset(); mocks.projects = []; mocks.rooms = {} })
afterEach(cleanup)

it('sends the admin to the machines page when no machine is online', async () => {
  serve([{ id: 'm1', name: 'Studio', status: 'offline' }])
  const { onOpenChange } = renderDialog()
  expect(await screen.findByText(/No machine is online/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Name')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Open machines' }))
  expect(onOpenChange).toHaveBeenCalledWith(false)
  expect(screen.getByText('machines page')).toBeInTheDocument()
})

it('picks the only online machine without showing a picker', async () => {
  serve([{ id: 'm1', name: 'Studio', status: 'online' }, { id: 'm2', name: 'Old', status: 'offline' }])
  renderDialog()
  expect(await screen.findByLabelText('Name')).toBeInTheDocument()
  expect(screen.queryByLabelText('Machine')).toBeNull()
  expect(screen.getByText('Runs on Studio')).toBeInTheDocument()
})

it('offers a picker of online machines when several are available', async () => {
  serve([{ id: 'm1', name: 'Studio', status: 'online' }, { id: 'm2', name: 'Laptop', status: 'online' }])
  renderDialog()
  const picker = await screen.findByLabelText('Machine')
  expect([...(picker as HTMLSelectElement).options].map(o => o.text)).toEqual(['Laptop', 'Studio'])
})

it('checks the room it was opened from (#902)', async () => {
  serve([{ id: 'm1', name: 'Studio', status: 'online' }])
  mocks.projects = [{ id: 'p', name: 'Demo' }]
  mocks.rooms = { p: [{ id: 'general', name: 'general', project_id: 'p', is_dm: false }] }
  renderDialog(['general'])
  fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Reviewer' } })
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getByLabelText('# general')).toBeChecked()
})
