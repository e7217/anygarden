// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import type { NodeUpdateControl, NodeUpdateState, PackageUpdate } from '@/hooks/useSystemVersion'

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

function control(over: Partial<NodeUpdateControl> = {}, state: Partial<NodeUpdateState> = {}): NodeUpdateControl {
  return {
    state: {
      supported: true,
      reason: null,
      status: 'idle',
      from_version: '0.18.0',
      target_version: null,
      error: null,
      ...state,
    },
    progress: { phase: 'idle', fromVersion: null, targetVersion: null, version: null, error: null, since: 0 },
    startError: null,
    start: vi.fn(async () => {}),
    ...over,
  }
}

describe('ServerVersionPanel node update (#927)', () => {
  it('[NODE-18] offers install-and-restart on a node that can update itself', async () => {
    updates = [row('0.18.0', '0.19.0', true)]
    const nodeUpdate = control()
    render(<ServerVersionPanel status={status()} nodeUpdate={nodeUpdate} />)
    expect(screen.queryByText('pip install -U anygarden')).toBeNull()
    // Without FeedbackProvider the default context asks window.confirm.
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Update to 0.19.0 and restart' }))
    await waitFor(() => expect(nodeUpdate.start).toHaveBeenCalledWith('0.19.0'))
  })

  it('[NODE-18] keeps the command and says why when the node cannot update itself', () => {
    updates = [row('0.18.0', '0.19.0', true)]
    render(
      <ServerVersionPanel
        status={status()}
        nodeUpdate={control({}, { supported: false, reason: 'development_install' })}
      />,
    )
    expect(screen.getByText('pip install -U anygarden')).toBeInTheDocument()
    expect(screen.getByText(/source checkout/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /and restart/ })).toBeNull()
  })

  it('[NODE-18] reports the version the node came back on and offers a reload', () => {
    updates = [row('0.18.0', '0.19.0', true)]
    const nodeUpdate = control({
      progress: { phase: 'updated', fromVersion: '0.18.0', targetVersion: '0.19.0', version: '0.19.0', error: null, since: 0 },
    })
    render(<ServerVersionPanel status={status()} nodeUpdate={nodeUpdate} />)
    expect(screen.getByRole('status')).toHaveTextContent('Updated to 0.19.0')
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /and restart/ })).toBeNull()
  })

  it('disables the button while the node installs', () => {
    updates = [row('0.18.0', '0.19.0', true)]
    const nodeUpdate = control({
      progress: { phase: 'installing', fromVersion: '0.18.0', targetVersion: '0.19.0', version: null, error: null, since: 0 },
    })
    render(<ServerVersionPanel status={status()} nodeUpdate={nodeUpdate} />)
    expect(screen.getByRole('button', { name: /and restart/ })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Installing 0.19.0')
  })
})
