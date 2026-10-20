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

const movable = { can_move: true, reason: null, files: 12, bytes: 3 * 1024 * 1024, limit_bytes: 200 * 1024 * 1024 }

function setup({
  trusted = false, rows = machines, onSubmit = vi.fn().mockResolvedValue(undefined),
  previewMove = vi.fn().mockResolvedValue(movable),
} = {}) {
  render(
    <MachineChangeDialog
      open
      onOpenChange={() => {}}
      agentId="agent-1"
      agentName="iac-agent"
      engine="codex-cli"
      trusted={trusted}
      currentMachineId="current"
      currentMachineName="iac-agent-machine"
      onSubmit={onSubmit}
      listMachines={async () => rows}
      listMachineEngines={async id => enginesByMachine[id] ?? []}
      previewMove={previewMove}
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
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('office', { moveFiles: true }))
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


describe('MachineChangeDialog work files (#915)', () => {
  it('offers to move work files and sends the choice [AGT-12]', async () => {
    const onSubmit = setup()
    const checkbox = await screen.findByTestId('machine-change-move-files-checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(true)
    expect(screen.getByTestId('machine-change-move-files')).toHaveTextContent('12 files, 3.0 MiB')
    fireEvent.click(checkbox)
    await waitFor(() => expect(screen.getByTestId('machine-change-submit')).toBeEnabled())
    fireEvent.click(screen.getByTestId('machine-change-submit'))
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('office', { moveFiles: false }))
  })

  it('explains why work files cannot move [AGT-12]', async () => {
    setup({ previewMove: vi.fn().mockResolvedValue({ can_move: false, reason: 'source_offline', files: 0, bytes: 0, limit_bytes: 1 }) })
    expect(await screen.findByTestId('machine-change-move-files-reason')).toHaveTextContent('current machine is offline')
    expect(screen.queryByTestId('machine-change-move-files-checkbox')).not.toBeInTheDocument()
  })

  it('names the size limit when there is too much to move', async () => {
    setup({ previewMove: vi.fn().mockResolvedValue({ can_move: false, reason: 'too_large', files: 20000, bytes: 300 * 1024 * 1024, limit_bytes: 200 * 1024 * 1024 }) })
    expect(await screen.findByTestId('machine-change-move-files-reason')).toHaveTextContent('300.0 MiB is over the 200.0 MiB limit')
  })
})
