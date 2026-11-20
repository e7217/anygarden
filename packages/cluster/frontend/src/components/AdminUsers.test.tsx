// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  confirm: vi.fn(async () => true),
  notify: vi.fn(),
}))
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u-admin', email: 'admin@example.com', is_admin: true } }),
}))
vi.mock('@/lib/api', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('@/components/feedback/FeedbackProvider', () => ({
  useFeedback: () => ({ confirm: mocks.confirm, notify: mocks.notify }),
}))

import AdminUsers, { type AdminUser } from './AdminUsers'

const json = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })

const ADMIN: AdminUser = {
  id: 'u-admin', email: 'admin@example.com', is_admin: true, disabled: false,
  created_at: '2026-11-01T00:00:00Z', last_login_at: '2026-11-20T00:00:00Z',
}
const MEMBER: AdminUser = {
  id: 'u-member', email: 'member@example.com', is_admin: false, disabled: false,
  created_at: '2026-11-02T00:00:00Z', last_login_at: null,
}

let settings = { registration_open: true }

beforeEach(() => {
  settings = { registration_open: true }
  mocks.apiFetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (path === '/api/v1/admin/users' && method === 'GET') return json([ADMIN, MEMBER])
    if (path === '/api/v1/admin/account-settings' && method === 'GET') return json(settings)
    if (path === '/api/v1/admin/account-settings' && method === 'PUT') {
      settings = JSON.parse(String(init?.body))
      return json(settings)
    }
    if (path === '/api/v1/admin/users/u-member' && method === 'PATCH') {
      return json({ ...MEMBER, ...JSON.parse(String(init?.body)) })
    }
    if (path === '/api/v1/admin/users' && method === 'POST') {
      const body = JSON.parse(String(init?.body))
      return json({ ...MEMBER, id: 'u-new', email: body.email, is_admin: body.is_admin }, 201)
    }
    throw new Error(`unexpected ${method} ${path}`)
  })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

it('[AUTH-17] lists accounts and locks self-suspension on your own row', async () => {
  render(<AdminUsers />)
  const own = await screen.findByTestId('user-row-admin@example.com')
  expect(within(own).getByText('You')).toBeInTheDocument()
  expect(within(own).getByRole('button', { name: /^Suspend/ })).toBeDisabled()
  expect(within(own).getByRole('button', { name: /^Remove administrator/ })).toBeDisabled()

  const member = screen.getByTestId('user-row-member@example.com')
  expect(within(member).getByText('Member')).toBeInTheDocument()
  expect(within(member).getByText(/Never signed in/)).toBeInTheDocument()
  expect(within(member).getByRole('button', { name: /^Suspend/ })).toBeEnabled()
})

it('[AUTH-17] suspends a member after confirmation', async () => {
  render(<AdminUsers />)
  const member = await screen.findByTestId('user-row-member@example.com')
  fireEvent.click(within(member).getByRole('button', { name: /^Suspend/ }))
  await waitFor(() => expect(within(screen.getByTestId('user-row-member@example.com')).getByText('Suspended')).toBeInTheDocument())
  expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }))
  expect(mocks.apiFetch).toHaveBeenCalledWith('/api/v1/admin/users/u-member', expect.objectContaining({
    method: 'PATCH', body: JSON.stringify({ disabled: true }),
  }))
})

it('[AUTH-17] filters by email', async () => {
  render(<AdminUsers />)
  await screen.findByTestId('user-row-member@example.com')
  fireEvent.change(screen.getByLabelText('Search by email'), { target: { value: 'MEMB' } })
  expect(screen.queryByTestId('user-row-admin@example.com')).not.toBeInTheDocument()
  expect(screen.getByTestId('user-row-member@example.com')).toBeInTheDocument()
})

it('[AUTH-17] adds a user from the dialog', async () => {
  render(<AdminUsers />)
  await screen.findByTestId('user-row-member@example.com')
  fireEvent.click(screen.getAllByRole('button', { name: 'Add user' })[0])
  const dialog = await screen.findByRole('dialog')
  fireEvent.change(within(dialog).getByLabelText('Email'), { target: { value: 'new@example.com' } })
  fireEvent.change(within(dialog).getByLabelText('Initial password'), { target: { value: 'initial-pass' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add user' }))
  expect(await screen.findByTestId('user-row-new@example.com')).toBeInTheDocument()
})

it('[AUTH-20] closes sign-up from the settings switch', async () => {
  render(<AdminUsers />)
  const checkbox = await screen.findByTestId('users-registration-open')
  expect(checkbox).toBeChecked()
  fireEvent.click(checkbox)
  await waitFor(() => expect(screen.getByTestId('users-registration-open')).not.toBeChecked())
  expect(settings.registration_open).toBe(false)
  expect(screen.getByText(/Sign-up is closed\. Add accounts/)).toBeInTheDocument()
})
