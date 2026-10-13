// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import SidebarCreateAgentDialog from './SidebarCreateAgentDialog'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/hooks/useRooms', () => ({
  useRooms: () => ({ projects: [], rooms: {}, status: 'ready', refetch: vi.fn() }),
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

function renderDialog() {
  const onOpenChange = vi.fn()
  render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<SidebarCreateAgentDialog open onOpenChange={onOpenChange}
          createAgent={vi.fn()} fetchEngineCatalog={vi.fn().mockResolvedValue(null)} availableEngines={[]} onCreated={vi.fn()} />} />
        <Route path="/admin/machines" element={<p>machines page</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return { onOpenChange }
}

beforeEach(() => mocks.apiFetch.mockReset())
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
