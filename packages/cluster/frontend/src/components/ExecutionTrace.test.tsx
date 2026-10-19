// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ExecutionTrace from './ExecutionTrace'
import { LocaleProvider } from '@/i18n/LocaleProvider'

const metadata = {
  execution_trace: {
    v: 1,
    steps: [
      { activities: { file_reads: 1 } },
      { narration: 'There are 4 new commits.\nSame procedure as before.', activities: { commands: 4, file_changes: 2 } },
    ],
    totals: { commands: 4, file_changes: 2, file_reads: 1 },
    truncated: 2,
  },
}

function renderTrace(meta: Record<string, unknown> | undefined) {
  return render(<LocaleProvider><ExecutionTrace metadata={meta} /></LocaleProvider>)
}

beforeEach(() => localStorage.setItem('anygarden_locale', 'en'))
afterEach(() => { cleanup(); localStorage.clear() })

describe('[ENG-11] ExecutionTrace', () => {
  it('starts collapsed with the totals on the toggle', () => {
    renderTrace(metadata)
    const toggle = screen.getByRole('button', { name: /Work · 4 shell commands · 2 files changed · 1 file read/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/There are 4 new commits/)).toBeNull()
  })

  it('reveals the narration and work of each step in order', () => {
    renderTrace(metadata)
    const toggle = screen.getByRole('button')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const steps = screen.getByRole('list')
    expect(toggle).toHaveAttribute('aria-controls', steps.id)
    expect(steps.textContent).toBe(
      '2 earlier steps not shown'
      + '1 file read'
      + 'There are 4 new commits.\nSame procedure as before.4 shell commands · 2 files changed',
    )
    fireEvent.click(toggle)
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('renders nothing without a usable trace', () => {
    const { container } = renderTrace({ execution_trace: { v: 1, steps: [], totals: {} } })
    expect(container).toBeEmptyDOMElement()
  })

  it('speaks Korean in a Korean UI', () => {
    localStorage.setItem('anygarden_locale', 'ko')
    renderTrace(metadata)
    expect(screen.getByRole('button', { name: /작업 과정 · 셸 명령 4개 · 파일 2개 수정 · 파일 1번 읽음/ })).toBeInTheDocument()
  })
})
