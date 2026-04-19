// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomEditDialog from './RoomEditDialog'

// ``apiFetch`` thin-wraps ``fetch`` (see lib/api.ts) — stubbing the
// global ``fetch`` mirrors what the dialog actually calls in the
// browser and keeps the mock surface as small as possible.
type FetchMock = ReturnType<typeof vi.fn>

function installFetch(responder: (url: string, init?: RequestInit) => Response): FetchMock {
  const mock = vi.fn().mockImplementation((url: string, init?: RequestInit) =>
    Promise.resolve(responder(url, init)),
  )
  globalThis.fetch = mock as unknown as typeof fetch
  return mock
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  localStorage.clear()
})

describe('RoomEditDialog – #148 context window toggle', () => {
  it('reflects the persisted context_window_enabled flag on open', async () => {
    installFetch((url, init) => {
      if (init?.method === 'PATCH') return jsonResponse({})
      // GET /api/v1/rooms/:id — return the current state.
      return jsonResponse({
        id: 'r1',
        name: 'general',
        description: 'ops',
        context_window_enabled: true,
      })
    })

    render(
      <RoomEditDialog
        roomId="r1"
        open={true}
        onOpenChange={() => {}}
      />,
    )

    const toggle = await screen.findByTestId(
      'room-edit-context-window-toggle',
    ) as HTMLInputElement
    await waitFor(() => {
      expect(toggle).toBeChecked()
    })
  })

  it('sends context_window_enabled in the PATCH body when toggled', async () => {
    const fetchMock = installFetch((url, init) => {
      if (init?.method === 'PATCH') return jsonResponse({})
      return jsonResponse({
        id: 'r1',
        name: 'general',
        description: null,
        context_window_enabled: false,
      })
    })

    const onSaved = vi.fn()
    const onOpenChange = vi.fn()

    render(
      <RoomEditDialog
        roomId="r1"
        open={true}
        onOpenChange={onOpenChange}
        onSaved={onSaved}
      />,
    )

    const toggle = (await screen.findByTestId(
      'room-edit-context-window-toggle',
    )) as HTMLInputElement

    await waitFor(() => {
      // Wait for initial load to settle so the default-false state
      // is seeded before we flip the box.
      expect(toggle).not.toBeChecked()
    })

    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patchCall).toBeDefined()
      const body = JSON.parse((patchCall![1] as RequestInit).body as string)
      expect(body.context_window_enabled).toBe(true)
    })
  })
})
