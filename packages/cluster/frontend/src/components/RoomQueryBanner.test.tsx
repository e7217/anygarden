// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import RoomQueryBanner, { type PendingQuery } from './RoomQueryBanner'

afterEach(() => cleanup())

function pending(overrides: Partial<PendingQuery> = {}): PendingQuery {
  return {
    query_id: 'q1',
    target_room_id: 't1',
    target_room_name: 'dev',
    status: 'pending',
    responded: 1,
    expected: 3,
    ...overrides,
  }
}

describe('RoomQueryBanner', () => {
  it('renders nothing when queries array is empty', () => {
    const { container } = render(
      <RoomQueryBanner queries={[]} onDismiss={() => {}} onScrollTo={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders pending chip with count and room name', () => {
    render(
      <RoomQueryBanner
        queries={[pending({ status: 'pending', responded: 1, expected: 3 })]}
        onDismiss={() => {}}
        onScrollTo={() => {}}
      />,
    )
    const chip = screen.getByTestId('room-query-chip-q1')
    expect(chip).toHaveAttribute('data-status', 'pending')
    expect(chip).toHaveTextContent('#dev')
    expect(chip).toHaveTextContent('1/3')
  })

  it('pending with no expected count shows "응답 대기 중"', () => {
    render(
      <RoomQueryBanner
        queries={[pending({ status: 'pending', responded: 0, expected: 0 })]}
        onDismiss={() => {}}
        onScrollTo={() => {}}
      />,
    )
    expect(screen.getByTestId('room-query-chip-q1')).toHaveTextContent(
      '응답 대기 중',
    )
  })

  it('renders completed chip and calls onScrollTo on click', () => {
    const onScrollTo = vi.fn()
    render(
      <RoomQueryBanner
        queries={[pending({ status: 'completed', responded: 3, expected: 3 })]}
        onDismiss={() => {}}
        onScrollTo={onScrollTo}
      />,
    )
    const chip = screen.getByTestId('room-query-chip-q1')
    expect(chip).toHaveAttribute('data-status', 'completed')
    fireEvent.click(chip)
    expect(onScrollTo).toHaveBeenCalledWith('q1')
  })

  it('renders timeout chip with missing-count hint and dismiss button', () => {
    const onDismiss = vi.fn()
    render(
      <RoomQueryBanner
        queries={[pending({ status: 'timeout', responded: 1, expected: 3 })]}
        onDismiss={onDismiss}
        onScrollTo={() => {}}
      />,
    )
    const chip = screen.getByTestId('room-query-chip-q1')
    expect(chip).toHaveAttribute('data-status', 'timeout')
    expect(chip).toHaveTextContent('2명 미응답')
    fireEvent.click(screen.getByLabelText('알림 닫기'))
    expect(onDismiss).toHaveBeenCalledWith('q1')
  })

  it('renders solo chip with explicit empty-target label and dismiss', () => {
    const onDismiss = vi.fn()
    render(
      <RoomQueryBanner
        queries={[pending({ status: 'solo', responded: 0, expected: 0 })]}
        onDismiss={onDismiss}
        onScrollTo={() => {}}
      />,
    )
    const chip = screen.getByTestId('room-query-chip-q1')
    expect(chip).toHaveAttribute('data-status', 'solo')
    expect(chip).toHaveTextContent('응답 가능 에이전트 없음')
    fireEvent.click(screen.getByLabelText('알림 닫기'))
    expect(onDismiss).toHaveBeenCalledWith('q1')
  })

  it('renders multiple chips side-by-side for parallel queries', () => {
    render(
      <RoomQueryBanner
        queries={[
          pending({ query_id: 'q1', target_room_name: 'dev' }),
          pending({ query_id: 'q2', target_room_name: 'ops', status: 'completed', responded: 2, expected: 2 }),
        ]}
        onDismiss={() => {}}
        onScrollTo={() => {}}
      />,
    )
    expect(screen.getByTestId('room-query-chip-q1')).toBeInTheDocument()
    expect(screen.getByTestId('room-query-chip-q2')).toBeInTheDocument()
  })

  it('uses role=status with polite aria-live for accessibility', () => {
    render(
      <RoomQueryBanner
        queries={[pending()]}
        onDismiss={() => {}}
        onScrollTo={() => {}}
      />,
    )
    const banner = screen.getByTestId('room-query-banner')
    expect(banner).toHaveAttribute('role', 'status')
    expect(banner).toHaveAttribute('aria-live', 'polite')
  })
})
