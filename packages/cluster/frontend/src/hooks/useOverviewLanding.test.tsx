// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { useOverviewLanding } from './useOverviewLanding'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))

const agent = (id: string) => ({ id, name: id, description: null, actual_state: 'running' })
const graph = (count: number) => new Response(JSON.stringify({ agents: Array.from({ length: count }, (_, i) => agent(`a${i}`)), links: [] }), { status: 200 })

function Home() {
  return <p>landing: {useOverviewLanding(true)}</p>
}
function renderHome() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/agents/links" element={<p>overview page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

afterEach(() => { cleanup(); mocks.apiFetch.mockReset() })

describe('useOverviewLanding', () => {
  it('opens the overview when there are two or more agents [ROOM-16]', async () => {
    mocks.apiFetch.mockResolvedValue(graph(2))
    renderHome()
    expect(screen.getByText('landing: checking')).toBeInTheDocument()
    expect(await screen.findByText('overview page')).toBeInTheDocument()
  })

  it('keeps the welcome screen for one agent or none [ROOM-16]', async () => {
    mocks.apiFetch.mockResolvedValue(graph(1))
    renderHome()
    expect(await screen.findByText('landing: welcome')).toBeInTheDocument()
  })

  it('falls back to the welcome screen when the check fails [ROOM-16]', async () => {
    mocks.apiFetch.mockResolvedValue(new Response('{}', { status: 500 }))
    renderHome()
    await waitFor(() => expect(screen.getByText('landing: welcome')).toBeInTheDocument())
  })
})
