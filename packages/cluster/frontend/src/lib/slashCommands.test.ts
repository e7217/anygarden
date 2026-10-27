import { describe, it, expect } from 'vitest'
import {
  matchSlashCommands,
  parseGoalCommand,
  parseRoutineCommand,
  parseSlashCommand,
  parseTaskCommand,
} from './slashCommands'

describe('parseSlashCommand', () => {
  it('returns null for non-slash input', () => {
    expect(parseSlashCommand('hello world')).toBeNull()
    expect(parseSlashCommand('')).toBeNull()
    expect(parseSlashCommand('  /task ...')).toBeNull() // leading space
  })

  it('returns null for unknown commands so the input falls through to a normal send', () => {
    expect(parseSlashCommand('/foo bar')).toBeNull()
  })

  it('routes /task to the task parser', () => {
    const result = parseSlashCommand('/task <@user:abc> design review')
    expect(result?.command).toBe('task')
    expect(result?.parsed.ok).toBe(true)
  })
})

describe('parseTaskCommand', () => {
  it('extracts assignee from an ID-based mention token', () => {
    const r = parseTaskCommand('<@user:abc123> design review')
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.payload.assignee_pid).toBe('abc123')
      expect(r.payload.title).toBe('design review')
    }
  })

  it('strips multiple mentions but keeps only the first as assignee', () => {
    const r = parseTaskCommand('<@user:a1> <@user:b2> ship the feature')
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.payload.assignee_pid).toBe('a1')
      // The second mention is dropped from the title — slash commands
      // accept exactly one assignee, and surfacing the rest in the
      // title would muddle the recorded task.
      expect(r.payload.title).toBe('ship the feature')
    }
  })

  it('rejects when no mention is present (assignee required)', () => {
    const r = parseTaskCommand('design review')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/assignee/i)
  })

  it('rejects when title is empty after stripping mention', () => {
    const r = parseTaskCommand('<@user:abc>   ')
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/title/i)
  })

  it('trims surrounding whitespace from the title', () => {
    const r = parseTaskCommand('<@user:abc>    review the layout   ')
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.payload.title).toBe('review the layout')
  })

  it('handles room mentions appearing inside the title', () => {
    // Room mention tokens (<#room:id>) are part of the title — they
    // are not assignee candidates. The parser should leave them in
    // place rather than stripping them.
    const r = parseTaskCommand('<@user:abc> sync with <#room:r1> team')
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.payload.assignee_pid).toBe('abc')
      expect(r.payload.title).toContain('<#room:r1>')
    }
  })
})

describe('parseRoutineCommand (#940)', () => {
  it('takes the first mention as the assignee and the rest as the title', () => {
    const r = parseRoutineCommand('<@user:a1> check host resources every morning')
    expect(r).toEqual({ ok: true, payload: { assignee_pid: 'a1', title: 'check host resources every morning' } })
  })

  it('opens an empty form when nothing follows the command', () => {
    expect(parseRoutineCommand('')).toEqual({ ok: true, payload: { assignee_pid: null, title: '' } })
  })

  it('keeps a title without an assignee', () => {
    expect(parseRoutineCommand('nightly backup')).toEqual({ ok: true, payload: { assignee_pid: null, title: 'nightly backup' } })
  })
})

describe('parseGoalCommand (#940)', () => {
  it('uses the text as the title and drops user mentions', () => {
    expect(parseGoalCommand('<@user:a1> raise parser accuracy')).toEqual({ ok: true, payload: { title: 'raise parser accuracy' } })
  })

  it('opens an empty form when nothing follows the command', () => {
    expect(parseGoalCommand('   ')).toEqual({ ok: true, payload: { title: '' } })
  })
})

describe('parseSlashCommand routing (#940)', () => {
  it('routes /routine and /goal, with or without a body', () => {
    expect(parseSlashCommand('/routine')?.command).toBe('routine')
    expect(parseSlashCommand('/routine <@user:a1> daily')?.command).toBe('routine')
    expect(parseSlashCommand('/goal')?.command).toBe('goal')
    expect(parseSlashCommand('/goal faster parser')?.command).toBe('goal')
  })

  it('does not treat a longer word as a known command', () => {
    expect(parseSlashCommand('/goals are nice')).toBeNull()
    expect(parseSlashCommand('/routines')).toBeNull()
  })
})

describe('matchSlashCommands (#940)', () => {
  it('lists every command for a bare slash', () => {
    expect(matchSlashCommands('/').map(c => c.name)).toEqual(['task', 'routine', 'goal'])
  })

  it('filters by the typed prefix', () => {
    expect(matchSlashCommands('/r').map(c => c.name)).toEqual(['routine'])
    expect(matchSlashCommands('/g').map(c => c.name)).toEqual(['goal'])
  })

  it('stops suggesting once the command word is finished or the input is not a command', () => {
    expect(matchSlashCommands('/goal ')).toEqual([])
    expect(matchSlashCommands('hello')).toEqual([])
    expect(matchSlashCommands('/zzz')).toEqual([])
  })
})
