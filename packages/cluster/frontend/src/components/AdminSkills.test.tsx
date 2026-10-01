// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const apiFetch = vi.fn()
vi.mock('@/lib/api', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }))

import AdminSkills from './AdminSkills'

function json(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body }
}

const searchCalls = () =>
  apiFetch.mock.calls.filter(([url]) => String(url).includes('/skills/search'))

beforeEach(() => {
  apiFetch.mockReset()
  apiFetch.mockImplementation(async (url: string) =>
    url.includes('/skills/search')
      ? json({ detail: 'skills.sh returned 503: upstream down' }, 502)
      : json([]),
  )
})
afterEach(cleanup)

async function openSearch() {
  render(<AdminSkills />)
  fireEvent.click(await screen.findByTestId('admin-skill-search-open'))
  return screen.findByTestId('admin-skill-search-input')
}

describe('AdminSkills skills.sh search (#773)', () => {
  it('does not search until the admin types a query', async () => {
    await openSearch()
    expect(searchCalls()).toHaveLength(0)
    expect(screen.getByTestId('admin-skill-search-submit')).toBeDisabled()
  })

  it('keeps one-character queries from reaching skills.sh', async () => {
    const input = await openSearch()
    fireEvent.change(input, { target: { value: 'a' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByTestId('admin-skill-search-submit')).toBeDisabled()
    expect(searchCalls()).toHaveLength(0)
  })

  it('shows a readable message instead of the raw upstream error', async () => {
    const input = await openSearch()
    fireEvent.change(input, { target: { value: 'design' } })
    fireEvent.click(screen.getByTestId('admin-skill-search-submit'))
    await waitFor(() => expect(searchCalls()).toHaveLength(1))
    const alert = await screen.findByRole('alert')
    expect(alert).not.toHaveTextContent('skills.sh returned')
  })
})
