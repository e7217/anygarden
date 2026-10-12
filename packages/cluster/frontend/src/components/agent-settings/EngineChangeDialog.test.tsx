// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

import EngineChangeDialog from './EngineChangeDialog'
import type { EngineCatalog } from '@/hooks/useAgents'

afterEach(() => cleanup())

const catalogs: Record<string, EngineCatalog> = {
  'codex-cli': { engine: 'codex-cli', default_model: 'gpt-6.1-sol', models: [{ id: 'gpt-6.1-sol', label: 'GPT-6.1 Sol' }], reasoning_levels: [] } as unknown as EngineCatalog,
  'pi-cli': { engine: 'pi-cli', default_model: '', models: [], reasoning_levels: [] } as unknown as EngineCatalog,
}

function setup(currentEngine: string, onSubmit = vi.fn().mockResolvedValue(undefined)) {
  render(
    <EngineChangeDialog
      open
      onOpenChange={() => {}}
      currentEngine={currentEngine}
      engines={['codex-cli', 'pi-cli']}
      fetchEngineCatalog={async engine => catalogs[engine] ?? null}
      onSubmit={onSubmit}
    />,
  )
  return onSubmit
}

describe('EngineChangeDialog (#769)', () => {
  it('offers only the other engines and submits codex with its default model', async () => {
    const onSubmit = setup('pi-cli')
    const engine = screen.getByTestId('engine-change-engine') as HTMLSelectElement
    expect([...engine.options].map(o => o.value)).toEqual(['codex-cli'])
    expect(screen.queryByTestId('engine-change-provider')).not.toBeInTheDocument()
    await screen.findByRole('option', { name: /gpt-6.1-sol/ })
    fireEvent.click(screen.getByTestId('engine-change-submit'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ engine: 'codex-cli', provider: null, model: null }))
  })

  it('asks Pi for provider and model before it can submit', async () => {
    const onSubmit = setup('codex-cli')
    const submit = screen.getByTestId('engine-change-submit')
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByTestId('engine-change-provider'), { target: { value: 'anthropic' } })
    fireEvent.change(screen.getByTestId('engine-change-model'), { target: { value: 'claude-x' } })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ engine: 'pi-cli', provider: 'anthropic', model: 'claude-x' }))
  })

  it('keeps the dialog open and shows the server error', async () => {
    setup('pi-cli', vi.fn().mockRejectedValue(new Error('Selected machine does not support this engine')))
    fireEvent.click(screen.getByTestId('engine-change-submit'))
    expect(await screen.findByRole('alert')).toHaveTextContent('does not support')
  })
})
