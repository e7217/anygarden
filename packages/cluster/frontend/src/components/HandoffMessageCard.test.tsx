// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import HandoffMessageCard from './HandoffMessageCard'
import type { HandoffMeta } from '@/lib/handoff'

afterEach(() => cleanup())

const handoff: HandoffMeta = {
  targetParticipantId: 'agent-target',
  instruction: 'Round 1 자기소개를 부탁드립니다.',
  nextSpeakerParticipantId: 'agent-target',
}

describe('HandoffMessageCard — states', () => {
  it('renders in pending state when resolvedAt is null', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card).toHaveAttribute('data-state', 'pending')
  })

  it('renders in resolved state when resolvedAt is provided', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={new Date().toISOString()}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card).toHaveAttribute('data-state', 'resolved')
  })

  it('renders in timeout state when pending has exceeded TIMEOUT_MS', () => {
    // 10 minutes ago — well beyond the 5-minute timeout.
    const oldDate = new Date(Date.now() - 10 * 60_000).toISOString()
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={oldDate}
        resolvedAt={null}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card).toHaveAttribute('data-state', 'timeout')
  })
})

describe('HandoffMessageCard — body render', () => {
  it('shows "→ <target>" caption with the target display name', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    const caption = screen.getByTestId('handoff-target-caption')
    expect(caption).toHaveTextContent('→')
    expect(caption).toHaveTextContent('agent01-gemini')
  })

  it('collapses the instruction body by default', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    // Instruction must not appear until the toggle is clicked.
    expect(screen.queryByTestId('handoff-instruction')).toBeNull()
  })

  it('expands the instruction body when the toggle is clicked', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    fireEvent.click(screen.getByTestId('handoff-toggle'))
    const body = screen.getByTestId('handoff-instruction')
    expect(body).toHaveTextContent('Round 1 자기소개를 부탁드립니다.')
  })

  it('omits the instruction block and toggle entirely when the body is empty', () => {
    // When the orchestrator emits ``[HANDOFF] <@user:x>`` with no
    // further instructions, the toggle is useless — hide it to avoid
    // flashing a "보기" button that reveals nothing.
    render(
      <HandoffMessageCard
        handoff={{ ...handoff, instruction: '' }}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    expect(screen.queryByTestId('handoff-toggle')).toBeNull()
  })
})

describe('HandoffMessageCard — accent border classes', () => {
  it('applies the pending sweep class so the animation runs', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={null}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card.className).toContain('handoff-card--pending')
  })

  it('applies the resolved accent class (static, no animation)', () => {
    render(
      <HandoffMessageCard
        handoff={handoff}
        targetName="agent01-gemini"
        createdAt={new Date().toISOString()}
        resolvedAt={new Date().toISOString()}
      />,
    )
    const card = screen.getByTestId('handoff-card')
    expect(card.className).toContain('handoff-card--resolved')
  })
})
