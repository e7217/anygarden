// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import ChatArea from './ChatArea'
import { LocaleProvider } from '@/i18n/LocaleProvider'
import type { Participant } from '@/pages/ChatPage'

vi.mock('@/hooks/useRooms', () => ({ useRooms: () => ({ rooms: {}, agentDMs: [] }) }))
vi.mock('@/hooks/useRoomFiles', () => ({ useRoomFiles: () => ({ files: [] }) }))
vi.mock('@/components/MessageBubble', () => ({
  default: ({ message }: { message: { content: string } }) => <div>{message.content}</div>,
}))
vi.mock('@/components/RoomQueryBanner', () => ({ default: () => null }))
vi.mock('@/components/EntityAvatar', () => ({ EntityAvatar: () => null }))
vi.mock('@/components/BrailleSpinner', () => ({ default: () => null }))

beforeAll(() => { Element.prototype.scrollIntoView = vi.fn() })
beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

const participants: Record<string, Participant> = {
  me: { id: 'me', display_name: 'Me', kind: 'user' },
  dev: { id: 'dev', display_name: 'dev', kind: 'agent' },
  pm: { id: 'pm', display_name: 'PM', kind: 'agent' },
}

const message = {
  type: 'message' as const, id: 'm1', room_id: 'r1', participant_id: 'me',
  content: 'go', seq: 1, created_at: '2026-10-19T00:00:00Z',
}

function renderArea(extra: Partial<Parameters<typeof ChatArea>[0]>) {
  return render(
    <LocaleProvider>
      <ChatArea messages={[message]} participants={participants} myParticipantId="me" {...extra} />
    </LocaleProvider>,
  )
}

describe('[ENG-10] ChatArea working indicator', () => {
  it('names the running work and shows the latest narration beside the spinner', () => {
    renderArea({
      typingUsers: new Set(['dev']),
      typingStages: { dev: 'using_tool' },
      typingWork: { dev: { activity: 'command', seq: 3, narration: 'There are 4 new commits.' } },
    })
    expect(screen.getByText('dev · running a shell command (#3)…')).toBeInTheDocument()
    expect(screen.getByTestId('typing-narration')).toHaveTextContent('There are 4 new commits.')
  })

  it('falls back to the stage and prefixes narration with names when several agents work', () => {
    renderArea({
      typingUsers: new Set(['dev', 'pm']),
      typingStages: { dev: 'writing', pm: 'preparing' },
      typingWork: { dev: { narration: 'Checking.' }, pm: { narration: 'Planning.' } },
    })
    expect(screen.getAllByTestId('typing-name').map(el => el.textContent)).toEqual([
      'dev · writing a response…,', 'PM · preparing a response…',
    ])
    expect(screen.getByTestId('typing-narration')).toHaveTextContent('dev: Checking. · PM: Planning.')
  })

  it('shows no narration line for an agent that sent none', () => {
    renderArea({ typingUsers: new Set(['dev']), typingStages: { dev: 'preparing' } })
    expect(screen.queryByTestId('typing-narration')).toBeNull()
  })
})

describe('[ENG-13] ChatArea working time', () => {
  afterEach(() => { vi.useRealTimers() })

  it('counts each agent\'s working time up beside its name, and shows none without a start', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    renderArea({
      typingUsers: new Set(['dev', 'pm']),
      typingStages: { dev: 'writing', pm: 'preparing' },
      typingSince: { dev: 1_000_000 - 151_000 },
    })
    const [dev, pm] = screen.getAllByTestId('typing-name')
    expect(dev).toHaveTextContent('dev · writing a response… · 2m 31s')
    expect(pm.querySelector('[data-testid="work-elapsed"]')).toBeNull()
    act(() => { vi.advanceTimersByTime(2_000) })
    expect(screen.getByTestId('work-elapsed')).toHaveTextContent('2m 33s')
  })
})
