// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { PackageUpdate } from '@/hooks/useSystemVersion'

let updates: PackageUpdate[] = []
vi.mock('@/hooks/useSystemVersion', () => ({
  useUpdateStatus: () => ({ updates, loading: false, error: null, refresh: vi.fn() }),
}))

import AdminSystem from './AdminSystem'

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

describe('AdminSystem version row (#773)', () => {
  it('labels the published version as PyPI, not "latest"', () => {
    updates = [row('0.19.0', '0.19.0', false)]
    render(<AdminSystem />)
    const versions = screen.getByText(
      (_, el) => el?.tagName === 'P' && /^current\s*0\.19\.0/.test(el.textContent ?? ''),
    )
    expect(versions.textContent).toMatch(/PyPI\s*0\.19\.0/)
    expect(versions.textContent).not.toMatch(/latest/)
  })

  it('marks a running build that is newer than PyPI', () => {
    updates = [row('0.19.0', '0.18.0', false)]
    render(<AdminSystem />)
    expect(screen.getByText('newer than PyPI')).toBeInTheDocument()
    expect(screen.queryByText('up to date')).toBeNull()
  })

  it('keeps "update available" when PyPI is ahead', () => {
    updates = [row('0.18.0', '0.19.0', true)]
    render(<AdminSystem />)
    expect(screen.getByText('update available')).toBeInTheDocument()
  })
})
