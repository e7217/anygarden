// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { LocaleProvider, LOCALE_STORAGE_KEY } from '@/i18n/LocaleProvider'
import ApiKeysDialog from './ApiKeysDialog'

const confirm = vi.fn()
vi.mock('@/components/feedback/FeedbackProvider', () => ({ useFeedback: () => ({ confirm }) }))

const existing = {
  id: 'k1',
  name: 'CI notifier',
  scope: 'read',
  prefix: 'agu_abcdefgh',
  created_at: '2026-11-01T00:00:00Z',
  expires_at: null,
  last_used_at: null,
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

let keys: unknown[]
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
  keys = [existing]
  confirm.mockReset().mockResolvedValue(true)
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (method === 'GET') return json(keys)
    if (method === 'POST') {
      const body = JSON.parse(String(init?.body))
      keys = [{ ...existing, id: 'k2', name: body.name, scope: body.scope }, ...keys]
      return json({ ...(keys[0] as object), key: 'agu_secret-plaintext' }, 201)
    }
    if (method === 'DELETE' && url.endsWith('/k1')) {
      keys = keys.filter((k) => (k as { id: string }).id !== 'k1')
      return new Response(null, { status: 204 })
    }
    return json({ detail: 'unexpected' }, 500)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderDialog(isAdmin = false) {
  return render(
    <LocaleProvider>
      <ApiKeysDialog open onOpenChange={() => {}} isAdmin={isAdmin} />
    </LocaleProvider>,
  )
}

describe('ApiKeysDialog', () => {
  it('shows a new key once and lists keys without their plaintext', async () => {
    renderDialog()
    expect(await screen.findByText('CI notifier · Read only')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Deploy bot' } })
    fireEvent.change(screen.getByLabelText('Access'), { target: { value: 'write' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create key' }))

    expect(await screen.findByLabelText('New API key')).toHaveValue('agu_secret-plaintext')
    expect(screen.getByText('Copy your new key now')).toBeInTheDocument()
    expect(await screen.findByText('Deploy bot · Read and write')).toBeInTheDocument()
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ name: 'Deploy bot', scope: 'write', expires_in_days: 90 })
  })

  it('offers the administrator scope only to admins', async () => {
    renderDialog(false)
    await screen.findByText('CI notifier · Read only')
    expect(screen.queryByRole('option', { name: 'Administrator' })).not.toBeInTheDocument()
    cleanup()

    renderDialog(true)
    await screen.findByText('CI notifier · Read only')
    expect(screen.getByRole('option', { name: 'Administrator' })).toBeInTheDocument()
  })

  it('revokes a key after confirmation', async () => {
    renderDialog()
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke CI notifier' }))
    await waitFor(() => expect(screen.getByText('No API keys yet.')).toBeInTheDocument())
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ destructive: true }))
  })

  it('keeps a key when revocation is cancelled', async () => {
    confirm.mockResolvedValue(false)
    renderDialog()
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke CI notifier' }))
    await waitFor(() => expect(confirm).toHaveBeenCalled())
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
    expect(screen.getByText('CI notifier · Read only')).toBeInTheDocument()
  })
})
