// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

import MachineChangeDialog, { type MachineChoice } from './MachineChangeDialog'

afterEach(() => cleanup())

const machines: MachineChoice[] = [
  { id: 'current', name: 'iac-agent-machine', status: 'online' },
  { id: 'office', name: 'office-linux', status: 'online' },
  { id: 'mac', name: 'mac-mini', status: 'online' },
  { id: 'gpu', name: 'gpu-box', status: 'offline' },
]
const enginesByMachine: Record<string, string[]> = { office: ['codex-cli'], mac: ['pi-cli'] }

function setup({ trusted = false, rows = machines, onSubmit = vi.fn().mockResolvedValue(undefined) } = {}) {
  render(
    <MachineChangeDialog
      open
      onOpenChange={() => {}}
      agentName="iac-agent"
      engine="codex-cli"
      trusted={trusted}
      currentMachineId="current"
      currentMachineName="iac-agent-machine"
      onSubmit={onSubmit}
      listMachines={async () => rows}
      listMachineEngines={async id => enginesByMachine[id] ?? []}
    />,
  )
  return onSubmit
}

describe('MachineChangeDialog (#907)', () => {
  it('offers other online machines, warns, and submits the choice [AGT-10]', async () => {
    const onSubmit = setup()
    const select = await screen.findByTestId('machine-change-target') as HTMLSelectElement
    expect([...select.options].map(o => o.value)).toEqual(['office', 'mac'])
    expect(screen.getByTestId('machine-change-warning')).toHaveTextContent('office-linux')
    expect(screen.getByTestId('machine-change-warning').querySelectorAll('li')).toHaveLength(2)
    await waitFor(() => expect(screen.getByTestId('machine-change-submit')).toBeEnabled())
    fireEvent.click(screen.getByTestId('machine-change-submit'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('office'))
  })

  it('blocks a machine that cannot run the engine [AGT-10]', async () => {
    setup()
    const select = await screen.findByTestId('machine-change-target')
    fireEvent.change(select, { target: { value: 'mac' } })
    expect(await screen.findByTestId('machine-change-engine-missing')).toHaveTextContent('mac-mini')
    expect(screen.getByTestId('machine-change-submit')).toBeDisabled()
  })

  it('adds the host-access warning for a trusted agent', async () => {
    setup({ trusted: true })
    await screen.findByTestId('machine-change-target')
    expect(screen.getByTestId('machine-change-warning').querySelectorAll('li')).toHaveLength(3)
  })

  it('explains when there is nowhere to move', async () => {
    setup({ rows: [machines[0], machines[3]] })
    expect(await screen.findByTestId('machine-change-empty')).toBeInTheDocument()
    expect(screen.getByTestId('machine-change-submit')).toBeDisabled()
  })
})
