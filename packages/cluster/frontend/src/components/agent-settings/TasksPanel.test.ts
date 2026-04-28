// @vitest-environment jsdom
// Unit tests for TasksPanel's grouping helper (#320).
//
// The previous version silently absorbed unknown statuses into the
// ``todo`` bucket. Real ``failed`` tasks (set by the goals sweeper)
// therefore showed up under "Todo" in the UI. These tests pin the new
// taxonomy so a future status addition has to be a deliberate code
// change, not an accidental "Todo + everything else" leak.
import { describe, it, expect, vi } from 'vitest'

import { groupTasksByStatus, STATUS_ORDER, type AgentTask } from './TasksPanel'

function task(partial: Partial<AgentTask>): AgentTask {
  return {
    id: partial.id ?? 't-' + Math.random().toString(36).slice(2, 8),
    room_id: partial.room_id ?? 'room-1',
    room_name: partial.room_name ?? 'Room',
    title: partial.title ?? 'task',
    status: partial.status ?? 'todo',
    assignee_participant_id: partial.assignee_participant_id ?? null,
    created_by: partial.created_by ?? null,
    created_at: partial.created_at ?? '2026-04-29T00:00:00Z',
  }
}

describe('groupTasksByStatus', () => {
  it('exposes the five canonical buckets in display order', () => {
    expect(STATUS_ORDER).toEqual([
      'todo',
      'in_progress',
      'blocked',
      'done',
      'failed',
    ])
  })

  it('routes failed tasks into their own bucket (regression for #320)', () => {
    const tasks = [
      task({ id: 'a', status: 'todo' }),
      task({ id: 'b', status: 'failed' }),
      task({ id: 'c', status: 'failed' }),
    ]
    const grouped = groupTasksByStatus(tasks)
    expect(grouped.failed.map(t => t.id)).toEqual(['b', 'c'])
    // The previous fallback would have stuffed b/c here.
    expect(grouped.todo.map(t => t.id)).toEqual(['a'])
  })

  it('groups every canonical status independently', () => {
    const tasks = [
      task({ id: '1', status: 'todo' }),
      task({ id: '2', status: 'in_progress' }),
      task({ id: '3', status: 'blocked' }),
      task({ id: '4', status: 'done' }),
      task({ id: '5', status: 'failed' }),
    ]
    const grouped = groupTasksByStatus(tasks)
    expect(grouped.todo.map(t => t.id)).toEqual(['1'])
    expect(grouped.in_progress.map(t => t.id)).toEqual(['2'])
    expect(grouped.blocked.map(t => t.id)).toEqual(['3'])
    expect(grouped.done.map(t => t.id)).toEqual(['4'])
    expect(grouped.failed.map(t => t.id)).toEqual(['5'])
  })

  it('drops unknown statuses with a warning rather than absorbing into todo', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    try {
      const grouped = groupTasksByStatus([
        task({ id: 'a', status: 'todo' }),
        task({ id: 'x', status: 'wat' }),
      ])
      expect(grouped.todo.map(t => t.id)).toEqual(['a'])
      // 'wat' is not silently re-routed into any other bucket.
      for (const status of STATUS_ORDER) {
        expect(grouped[status].some(t => t.id === 'x')).toBe(false)
      }
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining('unknown task status: wat'),
      )
    } finally {
      warn.mockRestore()
    }
  })

  it('preserves input order within each bucket', () => {
    const tasks = [
      task({ id: 'd1', status: 'done', created_at: '2026-04-01T00:00:00Z' }),
      task({ id: 'd2', status: 'done', created_at: '2026-04-02T00:00:00Z' }),
      task({ id: 'd3', status: 'done', created_at: '2026-04-03T00:00:00Z' }),
    ]
    expect(groupTasksByStatus(tasks).done.map(t => t.id)).toEqual([
      'd1',
      'd2',
      'd3',
    ])
  })
})
