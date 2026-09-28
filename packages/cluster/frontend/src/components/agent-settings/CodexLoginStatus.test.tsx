// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
import CodexLoginStatus from './CodexLoginStatus'

afterEach(() => { cleanup(); mocks.apiFetch.mockReset(); vi.useRealTimers() })

function engines(auth_status: string | null, auth_checked_at: string | null = '2026-09-28T00:00:00Z') {
  return { ok: true, json: async () => [{ engine: 'pi-cli' }, { engine: 'codex-cli', auth_status, auth_checked_at }] }
}

it.each([
  ['chatgpt', 'Signed in · ChatGPT account'],
  ['api_key', 'Signed in · OpenAI API key'],
  ['other', 'Signed in'],
  ['none', 'Sign-in required'],
  [null, 'Sign-in status unknown'],
  ['surprise', 'Sign-in status unknown'],
])('shows %s as "%s"', async (auth, title) => {
  mocks.apiFetch.mockResolvedValue(engines(auth))
  render(<CodexLoginStatus machineId="m1" machineName="home-server" />)
  const box = await screen.findByTestId('codex-login-status')
  expect(box).toHaveTextContent(title)
  expect(box).toHaveTextContent('home-server')
  expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/machines/m1/engines')
})

it('asks the machine to check again and shows the newer report', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  let report = engines('none', '2026-09-28T00:00:00Z')
  mocks.apiFetch.mockImplementation(async (path: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      report = engines('chatgpt', '2026-09-28T00:05:00Z')
      return { ok: true, json: async () => ({ status: 'checking' }) }
    }
    return report
  })
  render(<CodexLoginStatus machineId="m1" />)
  expect(await screen.findByText('Sign-in required')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Check again' }))
  expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/machines/m1/engines/codex-cli/check', { method: 'POST' })
  await vi.advanceTimersByTimeAsync(1600)
  await waitFor(() => expect(screen.getByTestId('codex-login-status')).toHaveAttribute('data-status', 'chatgpt'))
  expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled()
})
