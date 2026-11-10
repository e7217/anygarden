// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PeerChainProgressCard from './PeerChainProgressCard'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import { PEER_CHAINS_POLL_MS, usePeerChains } from '@/hooks/usePeerChains'
import { sampleChain } from '@/lib/peerChains.fixtures'
import type { ChatMessage } from '@/hooks/useWebSocket'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

async function flush() {
  await act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve() })
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function renderCard(locale: 'ko' | 'en', props: Partial<Parameters<typeof PeerChainProgressCard>[0]> = {}) {
  localStorage.setItem('anygarden_locale', locale)
  return render(
    <LocaleProvider>
      <PeerChainProgressCard chain={sampleChain()} {...props} />
    </LocaleProvider>,
  )
}

describe('PeerChainProgressCard', () => {
  it('sums up a running chain in one line', () => {
    renderCard('ko', { chain: sampleChain({ root: { participant_id: 'p', agent_id: 'a', name: '개발자' } }) })
    expect(screen.getByTestId('peer-chain-progress')).toHaveTextContent(
      '개발자 · 답을 모으는 중 · 7명 관여 · 지금: G 답변 중',
    )
  })

  it('renders nothing once the chain has finished', () => {
    renderCard('en', { chain: sampleChain({ state: 'done' }), onOpenThread: vi.fn() })
    expect(screen.queryByTestId('peer-chain-progress')).toBeNull()
  })

  it('opens the thread the questions live in', () => {
    const onOpenThread = vi.fn()
    renderCard('en', { onOpenThread })
    fireEvent.click(screen.getByRole('button', { name: 'Open thread' }))
    expect(onOpenThread).toHaveBeenCalledWith('human-1')
  })

  it('stops the whole chain only after confirmation', async () => {
    const onCancel = vi.fn().mockResolvedValue(true)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderCard('en', { onCancel })
    const stop = screen.getByRole('button', { name: 'Stop every agent in this question chain' })

    fireEvent.click(stop)
    await flush()
    expect(onCancel).not.toHaveBeenCalled()

    fireEvent.click(stop)
    await flush()
    expect(confirmSpy).toHaveBeenCalledTimes(2)
    expect(onCancel).toHaveBeenCalledWith('chain-1')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('says so when stopping fails', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderCard('en', { onCancel: vi.fn().mockResolvedValue(false) })
    fireEvent.click(screen.getByRole('button', { name: 'Stop every agent in this question chain' }))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('Could not stop the question chain.')
  })

  it('hides the controls the viewer cannot use', () => {
    renderCard('en')
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('usePeerChains', () => {
  const messages: ChatMessage[] = []

  it('polls while a chain is active and stops once none is', async () => {
    vi.useFakeTimers()
    fetchMock
      .mockResolvedValueOnce(json({ chains: [sampleChain()] }))
      .mockResolvedValueOnce(json({ chains: [sampleChain({ state: 'done' })] }))
    const { result } = renderHook(() => usePeerChains('room-1', messages, []))
    await flush()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/rooms/room-1/peer-chains?limit=20', expect.anything())
    expect(result.current.chains[0].state).toBe('active')

    await act(async () => { vi.advanceTimersByTime(PEER_CHAINS_POLL_MS) })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(result.current.chains[0].state).toBe('done')

    await act(async () => { vi.advanceTimersByTime(PEER_CHAINS_POLL_MS * 3) })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('refetches when a new question message arrives', async () => {
    fetchMock.mockResolvedValue(json({ chains: [] }))
    const question: ChatMessage = {
      type: 'message', id: 'q-1', room_id: 'room-1', participant_id: 'p-a', content: '?',
      seq: 2, created_at: '2026-11-10T00:00:00Z', metadata: { kind: 'peer_query' },
    }
    const { rerender } = renderHook(({ msgs }) => usePeerChains('room-1', msgs, []), {
      initialProps: { msgs: messages },
    })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    rerender({ msgs: [question] })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('posts a cancel and marks the chain stopped', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ chains: [sampleChain()] }))
      .mockResolvedValueOnce(json({ cancelled_turns: 3 }))
      .mockResolvedValue(json({ chains: [sampleChain({ state: 'cancelled' })] }))
    const { result } = renderHook(() => usePeerChains('room-1', messages, []))
    await flush()
    let ok = false
    await act(async () => { ok = await result.current.cancel('chain-1') })
    expect(ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/rooms/room-1/peer-chains/chain-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(result.current.chains[0].state).toBe('cancelled')
  })

  it('does nothing without a room', async () => {
    const { result } = renderHook(() => usePeerChains(null, messages, []))
    await flush()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(result.current.chains).toEqual([])
  })
})
