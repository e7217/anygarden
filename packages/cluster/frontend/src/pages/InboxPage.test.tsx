// @vitest-environment jsdom
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import InboxPage from './InboxPage'
import type { InboxItem } from '@/hooks/useProjectInbox'

const mocks = vi.hoisted(() => ({ items: [] as InboxItem[], loading: false, error: null as string | null, refresh: vi.fn(), answer: vi.fn(), decide: vi.fn() }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ user: { id: 'user' } }) }))
vi.mock('@/components/PageShell', () => ({ default: ({ children }: { children: React.ReactNode }) => <main>{children}</main> }))
vi.mock('@/hooks/useProjectInbox', () => ({ useProjectInbox: () => ({ ...mocks, projects: [{ id: 'a', name: 'Project A' }, { id: 'b', name: 'Project B' }] }) }))

function question(overrides: Partial<InboxItem> = {}): InboxItem {
  return { id: 'question:q', type: 'question', project_id: 'a', project_name: 'Project A', execution_id: 'run',
    operating_room_id: 'room', operating_room_name: 'Operations', task_id: 'task', task_title: 'Choose display name',
    task_room_id: 'room', task_room_name: 'Research', source_message_id: null, task_href: '/rooms/room', source_href: null,
    status: 'pending', needs_action: true, current_action: 'answer', created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T01:00:00Z',
    question: { id: 'q', execution_id: 'run', operating_room_id: 'room', task_id: 'task', task_room_id: 'room',
      task_title: 'Choose display name', question: 'What name should appear in the document?', status: 'pending',
      can_answer: true, is_current: true, input_revision: 1, created_at: '2026-10-01T00:00:00Z' }, ...overrides }
}
function task(overrides: Partial<InboxItem> = {}): InboxItem {
  return { ...question(), id: 'task:t', type: 'task', task_id: 't', needs_action: false, current_action: 'none', status: 'in_progress', question: undefined,
    task: { id: 't', title: 'Review report', status: 'in_progress', error: null, result_version: 0, result_markdown: null, result_sha256: null,
      assignee_display_name: 'Researcher', artifacts: [] }, ...overrides }
}
function setup(path = '/inbox') { return render(<MemoryRouter initialEntries={[path]}><InboxPage /></MemoryRouter>) }
async function toggle(item: HTMLElement, open: boolean) { (item as HTMLDetailsElement).open = open; fireEvent(item, new Event('toggle')); await waitFor(() => expect(item.hasAttribute('open')).toBe(open)) }
beforeEach(() => {
  mocks.items = [question(), task()]; mocks.loading = false; mocks.error = null
  mocks.answer.mockReset(); mocks.refresh.mockReset(); mocks.decide.mockReset()
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => window.setTimeout(callback, 0))
  vi.stubGlobal('cancelAnimationFrame', window.clearTimeout)
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('inbox summary and details', () => {
  it('keeps long question bodies and answer actions collapsed by default', () => {
    setup()
    expect(screen.getByTestId('inbox-item-question:q')).not.toHaveAttribute('open')
    expect(screen.getByText('What name should appear in the document?')).not.toBeVisible()
    expect(screen.getByTestId('execution-request-answer-q')).not.toBeVisible()
    expect(within(screen.getByRole('region', { name: 'Needs attention' })).getByTestId('inbox-item-question:q')).toBeInTheDocument()
  })
  it('retains an answer draft through disclosure and refreshed item objects', async () => {
    const view = setup(); const item = screen.getByTestId('inbox-item-question:q')
    await toggle(item, true)
    const input = screen.getByTestId('execution-request-answer-q')
    fireEvent.change(input, { target: { value: 'Anygarden' } })
    await toggle(item, false)
    mocks.items = mocks.items.map(value => ({ ...value }))
    view.rerender(<MemoryRouter><InboxPage /></MemoryRouter>)
    await toggle(item, true)
    expect(input).toHaveValue('Anygarden')
    expect(mocks.answer).not.toHaveBeenCalled()
  })
  it('opens a direct link and keeps its item reachable when filters change', () => {
    setup('/inbox?item=question%3Aq')
    expect(screen.getByTestId('inbox-item-question:q')).toHaveAttribute('open')
    expect(screen.getByTestId('execution-request-answer-q')).toBeVisible()
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: 'b' } })
    expect(screen.getByTestId('inbox-item-question:q')).toBeVisible()
  })
  it('puts confirmed cancellation in history and preserves its result', async () => {
    mocks.items = [task({ disposition: 'cancelled', task: { ...task().task!, result_markdown: 'Saved result before cancellation' } })]
    setup()
    const history = screen.getByRole('region', { name: 'Results and previous items' })
    expect(history).toHaveTextContent('Cancellation confirmed')
    expect(screen.queryByRole('region', { name: 'In progress' })).toBeNull()
    await toggle(screen.getByTestId('inbox-item-task:t'), true)
    expect(screen.getByText('Saved result before cancellation')).toBeVisible()
  })
  it('does not expose answer actions for a question without answer permission', async () => {
    mocks.items = [question({ current_action: 'read', question: { ...question().question!, can_answer: false } })]
    setup(); await toggle(screen.getByTestId('inbox-item-question:q'), true)
    expect(screen.queryByTestId('execution-request-submit-q')).toBeNull()
    expect(screen.getByText('Awaiting a member response')).toBeVisible()
  })
  it('keeps failed records out of the in-progress group', () => {
    mocks.items = [task({ status: 'failed', task: { ...task().task!, status: 'failed' } })]
    setup()
    expect(screen.getByRole('region', { name: 'Results and previous items' })).toHaveTextContent('Failed')
    expect(screen.queryByRole('region', { name: 'In progress' })).toBeNull()
  })
  it('shows the task state for read-only work needing attention', () => {
    mocks.items = [task({ status: 'blocked', needs_action: true, current_action: 'read', task: { ...task().task!, status: 'blocked' } })]
    setup()
    const summary = screen.getByTestId('inbox-item-task:t').querySelector('summary')!
    expect(summary).toHaveTextContent('Blocked · Needs attention')
    expect(summary).not.toHaveTextContent('Awaiting a member response')
  })
  it('shows access loss for unavailable direct links', () => {
    mocks.items = []; setup('/inbox?item=question%3Amissing')
    expect(screen.getByText('This item is unavailable or you no longer have access.')).toBeVisible()
  })
})
