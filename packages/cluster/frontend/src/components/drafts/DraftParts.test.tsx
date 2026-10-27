// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { DraftFailedNote, DraftRequestNote, describeSchedule } from './DraftParts'
import { SLASH_FORM_OPEN_EVENT } from '@/lib/drafts'
import type { Participant } from '@/lib/participants'

afterEach(cleanup)

const participants: Record<string, Participant> = {
  p1: { id: 'p1', display_name: 'Builder', kind: 'agent', agent_id: 'agent-1' },
}

describe('describeSchedule (#948)', () => {
  const t = (key: string, vars?: Record<string, string | number>) => `${key}${vars ? JSON.stringify(vars) : ''}`

  it('puts common schedules into words and gives up on the rest', () => {
    expect(describeSchedule(t as never, 'cron', { cron: '0 9 * * *' })).toBe('chat.draft.daily{"time":"09:00"}')
    expect(describeSchedule(t as never, 'cron', { cron: '30 8 * * 1-5' })).toBe('chat.draft.weekdays{"time":"08:30"}')
    expect(describeSchedule(t as never, 'interval', { interval_seconds: 7200 })).toBe('chat.draft.everyHours{"n":2}')
    expect(describeSchedule(t as never, 'cron', { cron: '*/5 * * * *' })).toBeNull()
  })
})

describe('draft notices (#947, #948)', () => {
  it('[WSP-06] shows what the person asked and who drafts it', () => {
    render(<DraftRequestNote meta={{ id: 'd', kind: 'goal', description: 'make search faster', drafter_participant_id: 'p1' }} participants={participants} />)
    expect(screen.getByTestId('draft-request-note')).toHaveTextContent('Goal draft requested · drafted by Builder')
    expect(screen.getByText('make search faster')).toBeInTheDocument()
  })

  it('[WSP-06] offers the empty form when no draft came', () => {
    const opened = vi.fn()
    window.addEventListener(SLASH_FORM_OPEN_EVENT, opened)
    render(<DraftFailedNote meta={{ id: 'd', kind: 'routine', description: 'nightly backup', drafter_participant_id: 'p1', assignee_agent_id: 'agent-1' }} participants={participants} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open an empty form' }))
    expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({ command: 'routine', payload: { assignee_pid: 'p1', text: '' } })
    window.removeEventListener(SLASH_FORM_OPEN_EVENT, opened)
  })
})
