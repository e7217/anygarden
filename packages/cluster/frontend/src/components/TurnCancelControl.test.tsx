// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import TurnCancelControl from './TurnCancelControl'
import { LocaleProvider } from '@/i18n/LocaleProvider'

function renderEn(ui: Parameters<typeof render>[0]) {
  localStorage.setItem('anygarden_locale', 'en')
  return render(<LocaleProvider>{ui}</LocaleProvider>)
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const openTurn = {
  request_id: 'req-1',
  room_id: 'room-1',
  agent_id: 'agent-1',
  participant_id: 'p-agent',
  state: 'leased',
  created_at: '2026-10-12T00:00:00Z',
}

describe('TurnCancelControl', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('[ENG-09] shows a named stop button for an open turn and cancels it', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse([openTurn]))
      .mockResolvedValueOnce(jsonResponse({ request_id: 'req-1', state: 'cancelled', changed: true }))

    renderEn(<TurnCancelControl roomId="room-1" participantId="p-agent" agentName="Helper" />)

    const button = await screen.findByRole('button', { name: 'Stop Helper' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/turns/open?room_id=room-1',
      expect.anything(),
    )
    fireEvent.click(button)

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/turns/req-1/cancel',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Stop Helper' })).not.toBeInTheDocument(),
    )
  })

  it('[ENG-09] renders nothing when the participant has no open turn', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([{ ...openTurn, participant_id: 'someone-else' }]))
    renderEn(<TurnCancelControl roomId="room-1" participantId="p-agent" agentName="Helper" />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('[ENG-09] reports a failed cancel and keeps the control', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse([openTurn]))
      .mockResolvedValueOnce(jsonResponse({ detail: { code: 'TURN_COMPLETING' } }, 409))
    renderEn(<TurnCancelControl roomId="room-1" participantId="p-agent" agentName="Helper" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Stop Helper' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not stop the agent')
    expect(screen.getByRole('button', { name: 'Stop Helper' })).toBeInTheDocument()
  })
})
