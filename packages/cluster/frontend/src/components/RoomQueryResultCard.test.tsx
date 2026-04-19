// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomQueryResultCard from './RoomQueryResultCard'
import type { RoomQueryResultMeta } from '@/lib/room-query'

afterEach(() => cleanup())

function makeResult(overrides: Partial<RoomQueryResultMeta> = {}): RoomQueryResultMeta {
  return {
    query_id: 'q1',
    target_room_id: 'room-xyzabc',
    responded: 2,
    expected: 2,
    status: 'completed',
    responses: [
      { participant_id: 'agent-1', content: 'Answer A' },
      { participant_id: 'agent-2', content: 'Answer B' },
    ],
    ...overrides,
  }
}

describe('RoomQueryResultCard', () => {
  it('renders completed header with N/M count and expanded responses by default', () => {
    const names = new Map([
      ['agent-1', 'Alice'],
      ['agent-2', 'Bob'],
    ])
    render(
      <RoomQueryResultCard
        result={makeResult()}
        participantNames={names}
        targetRoomName="ops"
      />,
    )
    const card = screen.getByTestId('room-query-result-q1')
    expect(card).toHaveAttribute('data-status', 'completed')
    expect(card).toHaveTextContent('#ops')
    expect(card).toHaveTextContent('2/2 응답')
    // Default expanded — answers are visible
    expect(screen.getByText('Answer A')).toBeInTheDocument()
    expect(screen.getByText('Answer B')).toBeInTheDocument()
    expect(screen.getByText('@Alice')).toBeInTheDocument()
  })

  it('toggles a response card collapse on header click', () => {
    const names = new Map([['agent-1', 'Alice']])
    render(
      <RoomQueryResultCard
        result={makeResult({
          responses: [{ participant_id: 'agent-1', content: 'Answer A' }],
          responded: 1,
          expected: 1,
        })}
        participantNames={names}
        targetRoomName="ops"
      />,
    )
    expect(screen.getByText('Answer A')).toBeInTheDocument()
    const header = screen.getByText('@Alice').closest('button')!
    fireEvent.click(header)
    expect(screen.queryByText('Answer A')).not.toBeInTheDocument()
    fireEvent.click(header)
    expect(screen.getByText('Answer A')).toBeInTheDocument()
  })

  it('renders timeout header with K명 미응답 tail', () => {
    render(
      <RoomQueryResultCard
        result={makeResult({ status: 'timeout', responded: 1, expected: 3 })}
        participantNames={new Map()}
        targetRoomName="ops"
      />,
    )
    const card = screen.getByTestId('room-query-result-q1')
    expect(card).toHaveAttribute('data-status', 'timeout')
    expect(card).toHaveTextContent('1/3 응답')
    expect(card).toHaveTextContent('2명 미응답')
  })

  it('renders solo header and fallback empty-state text', () => {
    render(
      <RoomQueryResultCard
        result={makeResult({ status: 'solo', responded: 0, expected: 0, responses: [] })}
        participantNames={new Map()}
        targetRoomName="ops"
      />,
    )
    const card = screen.getByTestId('room-query-result-q1')
    expect(card).toHaveAttribute('data-status', 'solo')
    expect(card).toHaveTextContent('대상 방에 응답할 에이전트가 없음')
    expect(
      screen.getByText('이 방에서 응답할 에이전트를 찾지 못했습니다.'),
    ).toBeInTheDocument()
  })

  it('falls back to last-6 of participant_id when name is missing', () => {
    render(
      <RoomQueryResultCard
        result={makeResult({
          responses: [
            { participant_id: '11111111-2222-3333-4444-abcdef123456', content: 'X' },
          ],
          responded: 1,
          expected: 1,
        })}
        participantNames={new Map()}
        targetRoomName="ops"
      />,
    )
    // slice(-6) of the id above is '123456'
    expect(screen.getByText('@123456')).toBeInTheDocument()
  })

  it('prefers server-provided response name over participantNames map', () => {
    // #153 — cross-room responder names come from the
    // representative agent's server-side snapshot. The source-room
    // participants map won't contain the replying agent, so
    // ``r.name`` must take priority to avoid the @hex fallback.
    render(
      <RoomQueryResultCard
        result={makeResult({
          responses: [
            { participant_id: 'agent-1', name: 'Alice', content: 'Answer A' },
          ],
          responded: 1,
          expected: 1,
        })}
        // Map deliberately has a *different* name for the same pid
        // to prove ``r.name`` wins over the map.
        participantNames={new Map([['agent-1', 'SHOULD_NOT_SHOW']])}
        targetRoomName="ops"
      />,
    )
    expect(screen.getByText('@Alice')).toBeInTheDocument()
    expect(screen.queryByText('@SHOULD_NOT_SHOW')).not.toBeInTheDocument()
  })

  it('falls back to participantNames when response name is empty string', () => {
    // Server writes ``name=""`` when the sender isn't in the
    // candidate snapshot (agent joined mid-query). The card must
    // still try the source-room ``participantNames`` map before
    // falling through to the last-6 hex — empty strings must be
    // treated as "no name", not as a valid display.
    render(
      <RoomQueryResultCard
        result={makeResult({
          responses: [
            { participant_id: 'agent-1', name: '', content: 'Answer' },
          ],
          responded: 1,
          expected: 1,
        })}
        participantNames={new Map([['agent-1', 'LocalName']])}
        targetRoomName="ops"
      />,
    )
    expect(screen.getByText('@LocalName')).toBeInTheDocument()
  })

  it('falls back to #id-slice when targetRoomName is missing', () => {
    render(
      <RoomQueryResultCard
        result={makeResult({ target_room_id: '11112222' })}
        participantNames={new Map([['agent-1', 'A'], ['agent-2', 'B']])}
      />,
    )
    expect(screen.getByTestId('room-query-result-q1')).toHaveTextContent('#112222')
  })
})
