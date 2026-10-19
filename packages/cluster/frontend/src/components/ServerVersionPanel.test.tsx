// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { PackageUpdate } from '@/hooks/useSystemVersion'

let updates: PackageUpdate[] = []
const status = () => ({ updates, loading: false, error: null, refresh: vi.fn() })

import ServerVersionPanel from './ServerVersionPanel'

afterEach(cleanup)

function row(current: string, latest: string, update_available: boolean): PackageUpdate {
  return {
    package: 'anygarden',
    current,
    latest,
    update_available,
    checked_at: '2026-10-01T00:00:00Z',
    error: null,
  } as PackageUpdate
}

describe('ServerVersionPanel version row (#773)', () => {
  it('labels the published version as PyPI, not "latest"', () => {
    updates = [row('0.19.0', '0.19.0', false)]
    render(<ServerVersionPanel status={status()} />)
    const versions = screen.getByText(
      (_, el) => el?.tagName === 'P' && /^current\s*0\.19\.0/.test(el.textContent ?? ''),
    )
    expect(versions.textContent).toMatch(/PyPI\s*0\.19\.0/)
    expect(versions.textContent).not.toMatch(/latest/)
  })

  it('marks a running build that is newer than PyPI', () => {
    updates = [row('0.19.0', '0.18.0', false)]
    render(<ServerVersionPanel status={status()} />)
    expect(screen.getByText('newer than PyPI')).toBeInTheDocument()
    expect(screen.queryByText('up to date')).toBeNull()
  })

  it('keeps "update available" when PyPI is ahead', () => {
    updates = [row('0.18.0', '0.19.0', true)]
    render(<ServerVersionPanel status={status()} />)
    expect(screen.getByText('update available')).toBeInTheDocument()
  })
})
