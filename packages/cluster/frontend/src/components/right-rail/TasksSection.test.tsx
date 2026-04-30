// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
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
  return render(<TasksSection roomId={ROOM} participants={participants} />)
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
    expect(assigneeSelect.parentElement?.className).toContain('flex-[0_1_8rem]')
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
