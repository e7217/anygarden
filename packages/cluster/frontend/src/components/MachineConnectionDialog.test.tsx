// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import MachineConnectionDialog, { machineRunCommand, machineServerUrl } from './MachineConnectionDialog'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const machine = { id: 'machine-123', name: 'Office worker', status: 'offline' }

describe('machine connection guide', () => {
  it('keeps the one-time token accessible after a failed list refresh, and excludes it from copied commands', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<MachineConnectionDialog open onOpenChange={vi.fn()} machine={machine} token="secret-machine-token" refreshWarning="Machine saved, list refresh failed" onCheck={vi.fn()} />)
    expect(screen.getByText('secret-machine-token')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Machine saved, list refresh failed')
    fireEvent.change(screen.getByLabelText('Anygarden server URL'), { target: { value: 'https://garden.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Copy Run command (Bash)' }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    const command = writeText.mock.calls[0][0] as string
    expect(command).toContain('--server \'https://garden.example.com\'')
    expect(command).toContain('--machine-id \'machine-123\'')
    expect(command).toContain('anygarden-machine connect')
    expect(command).toContain('&&\nanygarden-machine run')
    expect(command).not.toContain('secret-machine-token')
    expect(screen.getByText(/settings and token privately on that computer/)).toBeInTheDocument()
    expect(screen.getByText('Restart and automatic startup')).toBeInTheDocument()
  })

  it('shows localhost guidance and prevents copying an invalid server command', () => {
    render(<MachineConnectionDialog open onOpenChange={vi.fn()} machine={machine} onCheck={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Anygarden server URL'), { target: { value: 'http://localhost:8001' } })
    expect(screen.getByText(/localhost points to the new computer/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Anygarden server URL'), { target: { value: 'https://user:password@garden.example.com' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Enter an HTTP(S) server address')
    expect(screen.queryByRole('button', { name: 'Copy Run command (Bash)' })).not.toBeInTheDocument()
  })

  it('verifies actual machine status and offers retry after a failed check', async () => {
    const onCheck = vi.fn().mockRejectedValueOnce(new Error('Network down')).mockResolvedValueOnce(undefined)
    const { rerender } = render(<MachineConnectionDialog open onOpenChange={vi.fn()} machine={machine} onCheck={onCheck} />)
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not check the connection')
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }))
    await waitFor(() => expect(onCheck).toHaveBeenCalledTimes(2))
    rerender(<MachineConnectionDialog open onOpenChange={vi.fn()} machine={{ ...machine, status: 'online' }} onCheck={onCheck} />)
    expect(screen.getByRole('status')).toHaveTextContent('Connected.')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports clipboard failures with a manual-copy recovery path', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) }, configurable: true })
    render(<MachineConnectionDialog open onOpenChange={vi.fn()} machine={machine} token="secret" onCheck={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Copy Machine token' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Select and copy the text manually')
    expect(screen.getByText('secret')).toBeInTheDocument()
  })
})

describe('machine server address validation', () => {
  it.each(['file:///tmp/server', 'ssh://garden.example.com', 'https://garden.example.com?token=secret', 'https://garden.example.com/#setup'])('rejects a non-server address: %s', value => {
    expect(machineServerUrl(value)).toBeNull()
  })

  it('preserves a reverse-proxy base path and safely quotes shell metacharacters', () => {
    const server = machineServerUrl("https://garden.example.com/any'garden")!
    const command = machineRunCommand(server, 'machine-1', false)
    expect(command).toContain("--server 'https://garden.example.com/any'\"'\"'garden'")
    expect(command).not.toContain('--token')
  })
})
