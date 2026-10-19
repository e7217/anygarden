// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({ setup: vi.fn() }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ login: vi.fn(), register: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async (path: string) => {
  if (path === '/api/v1/auth/setup') return mocks.setup()
  throw new Error(`unexpected ${path}`)
}) }))

import LoginForm from './LoginForm'
afterEach(() => { cleanup(); vi.clearAllMocks() })

const selectedTab = () => screen.getByRole('tab', { selected: true })

it('[AUTH-10] opens on Create account while the node has no account yet', async () => {
  mocks.setup.mockResolvedValue({ ok: true, json: async () => ({ needs_first_account: true }) })
  render(<LoginForm />)
  await waitFor(() => expect(selectedTab()).toHaveTextContent('Create account'))
})

it('[AUTH-10] stays on Log in once an account exists', async () => {
  mocks.setup.mockResolvedValue({ ok: true, json: async () => ({ needs_first_account: false }) })
  render(<LoginForm />)
  await waitFor(() => expect(mocks.setup).toHaveBeenCalled())
  expect(selectedTab()).toHaveTextContent('Log in')
})

it('[AUTH-10] falls back to Log in when the setup check fails', async () => {
  mocks.setup.mockRejectedValue(new Error('offline'))
  render(<LoginForm />)
  await waitFor(() => expect(mocks.setup).toHaveBeenCalled())
  expect(selectedTab()).toHaveTextContent('Log in')
})
