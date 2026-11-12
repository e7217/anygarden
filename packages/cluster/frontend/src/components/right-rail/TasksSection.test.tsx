// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import TasksSection from './TasksSection'
import type { Task } from '@/hooks/useRoomTasks'
import type { Participant } from '@/pages/ChatPage'

const mocks = vi.hoisted(() => ({
  tasks: [] as Task[],
  refresh: vi.fn(() => Promise.resolve()),
  create: vi.fn(() => Promise.resolve(null)),
  update: vi.fn(() => Promise.resolve()),
  remove: vi.fn(() => Promise.resolve()),
  autoRouteUnassigned: vi.fn(() =>
    Promise.resolve({
      routed: [] as { task_id: string; assignee_agent_id: string }[],
      skipped: [] as { task_id: string; reason: string }[],
      rep_agent_id: 'agent-rep',
      request_id: 'req-1',
    }),
  ),
}))

vi.mock('@/hooks/useRoomTasks', () => ({
  useRoomTasks: () => ({
    tasks: mocks.tasks,
    loading: false,
    error: null,
    refresh: mocks.refresh,
    create: mocks.create,
    update: mocks.update,
    remove: mocks.remove,
  }),
}))

vi.mock('@/lib/routing', () => ({
  autoRouteUnassigned: mocks.autoRouteUnassigned,
}))

const ROOM = 'room-1'
const LONG_TASK_TITLE =
  'Investigate a very long right rail task title that must stay inside the context rail'
const LONG_AGENT_NAME = 'team-alpha-agent01-claude-long-name'

const participants: Record<string, Participant> = {
  p1: {
    id: 'p1',
    display_name: LONG_AGENT_NAME,
    kind: 'agent',
    agent_id: 'agent-1',
  },
  p2: {
    id: 'p2',
    display_name: 'agent02-codex',
    kind: 'agent',
    agent_id: 'agent-2',
  },
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    room_id: ROOM,
    title: LONG_TASK_TITLE,
    status: 'todo',
    assignee_participant_id: 'p1',
    created_at: '2026-04-30T00:00:00Z',
    ...overrides,
  }
}

function renderTasksSection() {
  return render(<MemoryRouter><TasksSection roomId={ROOM} participants={participants} /></MemoryRouter>)
}

beforeEach(() => {
  mocks.tasks = [task()]
  mocks.refresh.mockClear()
  mocks.create.mockClear()
  mocks.update.mockClear()
  mocks.remove.mockClear()
  mocks.autoRouteUnassigned.mockReset()
  mocks.autoRouteUnassigned.mockResolvedValue({
    routed: [],
    skipped: [],
    rep_agent_id: 'agent-rep',
    request_id: 'req-1',
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('TasksSection right-rail containment', () => {
  it('renders task rows with shrink-safe title and assignee slots', () => {
    renderTasksSection()

    const row = screen.getByTestId('right-rail-task-row-task-1')
    expect(row.className).toContain('min-w-0')

    const title = screen.getByText(LONG_TASK_TITLE)
    expect(title.className).toContain('min-w-0')
    expect(title.className).toContain('truncate')

    const assigneeSelect = screen.getByTestId('right-rail-task-assignee-task-1')
    expect(assigneeSelect).toBeInTheDocument()
    expect(assigneeSelect.className).toContain('min-w-0')
    expect(assigneeSelect.className).toContain('max-w-full')
    expect(assigneeSelect.parentElement?.className).toContain('flex-[0_1_auto]')
    expect(assigneeSelect.parentElement?.className).toContain('min-w-[5rem]')
  })

  it('keeps the create assignee control shrink-safe next to the create button', () => {
    renderTasksSection()

    const createAssignee = screen.getByTestId('right-rail-task-create-assignee')
    expect(createAssignee.className).toContain('min-w-0')
    expect(createAssignee.className).toContain('flex-1')
    expect(createAssignee.parentElement?.className).toContain('min-w-0')
  })

  it('wraps long auto-route toast text inside the rail', async () => {
    mocks.tasks = [task({ assignee_participant_id: null })]
    mocks.autoRouteUnassigned.mockResolvedValue({
      routed: [{ task_id: 'task-1', assignee_agent_id: 'agent-1' }],
      skipped: [{ task_id: 'task-2', reason: 'no candidate' }],
      rep_agent_id: 'agent-rep',
      request_id: 'req-1',
    })
    renderTasksSection()

    fireEvent.click(screen.getByTestId('right-rail-auto-route-button'))

    const toast = await screen.findByTestId('right-rail-route-toast')
    await waitFor(() => expect(toast).toHaveTextContent(LONG_AGENT_NAME))
    expect(toast.className).toContain('break-words')
  })
})

function openDialog(id = 'task-1') {
  fireEvent.click(screen.getByTestId(`right-rail-task-open-${id}`))
  return screen.getByTestId(`right-rail-task-dialog-${id}`)
}

describe('TasksSection flat list', () => {
  it('lists current work without per-status headings, ordered by status', () => {
    mocks.tasks = [
      task({ id: 'todo', status: 'todo' }),
      task({ id: 'doing', status: 'in_progress' }),
      task({ id: 'stuck', status: 'blocked' }),
    ]
    renderTasksSection()
    expect(screen.queryByText(/To do ·/)).not.toBeInTheDocument()
    expect(screen.queryByText(/In progress ·/)).not.toBeInTheDocument()
    const ids = screen.getAllByTestId(/^right-rail-task-row-/).map(row => row.dataset.testid)
    expect(ids).toEqual(['right-rail-task-row-doing', 'right-rail-task-row-stuck', 'right-rail-task-row-todo'])
  })
})

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()

describe('TasksSection row times', () => {
  it('shows how long running work has gone on instead of the assignee', () => {
    mocks.tasks = [task({ status: 'in_progress', started_at: ago(23 * 60_000) })]
    renderTasksSection()
    expect(screen.getByTestId('right-rail-task-meta-task-1')).toHaveTextContent('23m')
  })

  it('shows when finished work ended', () => {
    mocks.tasks = [task({ status: 'done', started_at: ago(4 * 3_600_000), finished_at: ago(2 * 3_600_000) })]
    renderTasksSection()
    expect(screen.getByTestId('right-rail-task-meta-task-1')).toHaveTextContent('2 hours ago')
  })

  it('keeps the assignee for waiting work', () => {
    renderTasksSection()
    expect(screen.getByTestId('right-rail-task-meta-task-1')).toHaveTextContent(LONG_AGENT_NAME)
  })
})

describe('TasksSection detail dialog', () => {
  it.each(['blocked', 'failed'])('keeps the reason for a %s task out of the row and shows it in the dialog', status => {
    mocks.tasks = [task({ status, error: 'Waiting for the job analysis result before editing the resume' })]
    renderTasksSection()
    expect(screen.queryByTestId('right-rail-task-reason-task-1')).not.toBeInTheDocument()
    openDialog()
    expect(screen.getByTestId('right-rail-task-reason-task-1')).toHaveTextContent('Waiting for the job analysis result')
  })

  it('renders supplied instructions, results, source versions and schedule evidence', () => {
    mocks.tasks = [task({
      status: 'done', spec: 'Use only verified career facts.', result_markdown: '**Resume ready** — verified career facts',
      dependency_results: [{ task_id: 'analysis-1', room_id: 'analysis-room', title: 'Job analysis', result_markdown: 'Requires Python experience', result_sha256: 'a'.repeat(64), finished_at: '2026-09-30T08:00:00Z' }],
      schedule_context: { goal_id: 'goal-career', scheduled_for: '2026-09-30T09:00:00Z', timezone: 'Asia/Seoul', overlap_policy: 'wait', trigger_source: 'scheduler' },
    })]
    renderTasksSection()
    expect(screen.queryByText('Use only verified career facts.')).not.toBeInTheDocument()
    const dialog = openDialog()
    expect(screen.getByText('Use only verified career facts.')).toBeInTheDocument()
    expect(screen.getByText('Resume ready').tagName).toBe('STRONG')
    expect(screen.getByText('Job analysis')).toBeInTheDocument()
    expect(screen.getByText('Requires Python experience')).toBeInTheDocument()
    expect(screen.getByText('analysis-1')).toBeInTheDocument()
    expect(screen.getByTitle('a'.repeat(64))).toHaveTextContent('SHA-256')
    expect(screen.getByText('Asia/Seoul')).toBeInTheDocument()
    expect(dialog.querySelector('time[datetime="2026-09-30T09:00:00Z"]')).not.toBeNull()
    expect(screen.getByText('Wait until the previous run finishes')).toBeInTheDocument()
    expect(mocks.update).not.toHaveBeenCalled()
  })

  it('saves an edited title from the dialog', async () => {
    renderTasksSection()
    openDialog()
    const save = screen.getByTestId('right-rail-task-dialog-save')
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByTestId('right-rail-task-dialog-title'), { target: { value: 'Renamed task' } })
    fireEvent.click(save)
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith('task-1', { title: 'Renamed task' }))
    await waitFor(() => expect(screen.queryByTestId('right-rail-task-dialog-task-1')).not.toBeInTheDocument())
  })

  it('draws the progress timeline in order and ends running work at now', () => {
    mocks.tasks = [task({
      status: 'in_progress',
      created_at: ago(30 * 60_000),
      assigned_at: ago(29 * 60_000),
      started_at: ago(23 * 60_000),
    })]
    renderTasksSection()
    openDialog()
    const items = Array.from(screen.getByTestId('right-rail-task-timeline-task-1').querySelectorAll('li')).map(li => li.textContent)
    expect(items).toHaveLength(4)
    expect(items[0]).toContain('Created')
    expect(items[1]).toContain(`Assigned to ${LONG_AGENT_NAME}`)
    expect(items[2]).toContain('Started')
    expect(items[3]).toContain('In progress · 23m')
    expect(items[3]).toContain('Now')
  })

  it('puts failed attempts on the timeline of finished work', () => {
    mocks.tasks = [task({
      status: 'done',
      created_at: ago(70 * 60_000),
      started_at: ago(65 * 60_000),
      finished_at: ago(5 * 60_000),
      recovery: { state: 'completed', attempts: [
        { ordinal: 1, state: 'failed', outcome: 'timeout', started_at: ago(65 * 60_000), finished_at: ago(40 * 60_000) },
        { ordinal: 2, state: 'completed', outcome: 'succeeded', started_at: ago(39 * 60_000), finished_at: ago(5 * 60_000) },
      ] },
    })]
    renderTasksSection()
    openDialog()
    const timeline = screen.getByTestId('right-rail-task-timeline-task-1')
    expect(timeline).toHaveTextContent('Attempt 1 started')
    expect(timeline).toHaveTextContent('Attempt 2 started')
    expect(timeline.querySelectorAll('li')[timeline.querySelectorAll('li').length - 1]).toHaveTextContent('Done')
    expect(screen.queryByTestId('task-recovery-history-task-1')).not.toBeInTheDocument()
  })

  it('shows run-owned tasks read-only', () => {
    mocks.tasks = [task({ status: 'in_progress', execution_id: 'run', disposition: 'current' })]
    renderTasksSection()
    openDialog()
    expect(screen.queryByTestId('right-rail-task-dialog-title')).not.toBeInTheDocument()
    expect(screen.queryByTestId('right-rail-task-dialog-delete')).not.toBeInTheDocument()
    expect(screen.getByText('This task can only be viewed here.')).toBeInTheDocument()
  })
})


describe('TasksSection current work and history', () => {
  it('puts cancelled and superseded tasks in history instead of current progress', () => {
    mocks.tasks = [
      task({ id: 'current', status: 'in_progress', execution_id: 'run', disposition: 'current' }),
      task({ id: 'cancelled', status: 'in_progress', execution_id: 'run', disposition: 'cancelled' }),
      task({ id: 'old', status: 'in_progress', execution_id: 'run', disposition: 'superseded' }),
    ]
    renderTasksSection()
    const history = screen.getByTestId('right-rail-task-history')
    expect(history).not.toHaveAttribute('open')
    expect(history).toContainElement(screen.getByTestId('right-rail-task-row-cancelled'))
    expect(history).toContainElement(screen.getByTestId('right-rail-task-row-old'))
    expect(history).not.toContainElement(screen.getByTestId('right-rail-task-row-current'))
    expect(openDialog('cancelled')).toHaveTextContent('Cancellation confirmed')
  })
  it('shows three recent results and retains every remaining result in history', () => {
    mocks.tasks = Array.from({ length: 5 }, (_, i) => task({ id: `done-${i}`, status: 'done', created_at: `2026-10-0${i + 1}T00:00:00Z` }))
    renderTasksSection()
    const recent = screen.getByTestId('right-rail-recent-results')
    expect(recent.querySelectorAll('article')).toHaveLength(3)
    expect(recent).toContainElement(screen.getByTestId('right-rail-task-row-done-4'))
    expect(screen.getByTestId('right-rail-task-history').querySelectorAll('article')).toHaveLength(2)
  })
})
