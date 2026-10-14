// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import TurnCancelControl from './TurnCancelControl'
import { LocaleProvider } from '@/i18n/LocaleProvider'

function renderEn(ui: Parameters<typeof render>[0]) {
  localStorage.setItem('anygarden_locale', 'en')
  return render(<LocaleProvider>{ui}</LocaleProvider>)
}

describe('TurnCancelControl', () => {
  afterEach(() => cleanup())

  it('[ENG-09] cancels every given turn from one named button', async () => {
    const onCancel = vi.fn().mockResolvedValue(true)
    renderEn(<TurnCancelControl requestIds={['req-1', 'req-2']} label="Stop Helper" onCancel={onCancel} />)
    fireEvent.click(screen.getByRole('button', { name: 'Stop Helper' }))
    await waitFor(() => expect(onCancel).toHaveBeenCalledWith(['req-1', 'req-2']))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('[ENG-09] renders nothing without an open turn', () => {
    renderEn(<TurnCancelControl requestIds={[]} label="Stop Helper" onCancel={vi.fn()} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('[ENG-09] shows visible text when given', () => {
    renderEn(<TurnCancelControl requestIds={['req-1']} label="Stop all work in this thread" text="Stop" onCancel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Stop all work in this thread' })).toHaveTextContent('Stop')
  })

  it('[ENG-09] reports a failed cancel and keeps the control', async () => {
    renderEn(<TurnCancelControl requestIds={['req-1']} label="Stop Helper" onCancel={vi.fn().mockResolvedValue(false)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Stop Helper' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not stop the agent')
    expect(screen.getByRole('button', { name: 'Stop Helper' })).toBeInTheDocument()
  })
})
