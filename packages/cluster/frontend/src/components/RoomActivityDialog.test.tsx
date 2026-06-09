// @vitest-environment jsdom
// #431 — RoomActivityDialog renders the A→B causal link as "↳ from
// <parent agent>". splitLogs' parentRequestId parsing is pinned in
// ActivityPanel.test.ts; this covers the dialog's parent *resolution*
// (turnById lookup) and its degradation when the parent is off-window.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomActivityDialog from './RoomActivityDialog'

type FetchMock = ReturnType<typeof vi.fn>

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// The dialog issues a single GET .../activity; everything else 200-{}.
function installFetch(logs: unknown[]): FetchMock {
  const mock = vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/activity')) {
      return Promise.resolve(jsonResponse(logs))
    }
    return Promise.resolve(jsonResponse({}))
  })
  globalThis.fetch = mock as unknown as typeof fetch
  return mock
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function messageReceived(
  rid: string,
  agentId: string,
  ts: string,
  parent?: string,
) {
  return {
    id: 'evt-' + rid,
    event_type: 'message_received',
    timestamp: ts,
    request_id: rid,
    agent_id: agentId,
    details: parent
      ? { parent_request_id: parent, trigger_message_id: 'm', room_id: 'room-1' }
      : { room_id: 'room-1' },
  }
}

describe('RoomActivityDialog parent (↳) rendering (#431)', () => {
  it('renders "↳ from <parent>" when the parent turn is in-window', async () => {
    installFetch([
      messageReceived('rid-A', 'aaaaaa11', '2026-06-09T12:00:00Z'),
      messageReceived('rid-B', 'bbbbbb22', '2026-06-09T12:00:02Z', 'rid-A'),
    ])
    render(<RoomActivityDialog roomId="room-1" open onOpenChange={() => {}} />)

    const markers = await screen.findAllByTestId('room-activity-parent')
    // Only the child turn (rid-B) shows the marker, labelled with the
    // parent agent's 6-char slice.
    expect(markers).toHaveLength(1)
    expect(markers[0]).toHaveTextContent('aaaaaa')
  })

  it('shows no ↳ marker when the parent turn is off-window', async () => {
    installFetch([
      messageReceived('rid-B', 'bbbbbb22', '2026-06-09T12:00:02Z', 'rid-missing'),
    ])
    render(<RoomActivityDialog roomId="room-1" open onOpenChange={() => {}} />)

    await screen.findAllByTestId('room-activity-turn')
    expect(screen.queryByTestId('room-activity-parent')).toBeNull()
  })

  it('shows no ↳ marker for a turn with no parent', async () => {
    installFetch([messageReceived('rid-A', 'aaaaaa11', '2026-06-09T12:00:00Z')])
    render(<RoomActivityDialog roomId="room-1" open onOpenChange={() => {}} />)

    await screen.findAllByTestId('room-activity-turn')
    expect(screen.queryByTestId('room-activity-parent')).toBeNull()
  })
})
