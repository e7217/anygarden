// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import AgentEditDialog from './AgentEditDialog'
import type { Agent, AgentFile } from '@/hooks/useAgents'

// jsdom doesn't implement the Object URL API, so stub them as
// callable no-ops before the download test spies on them.
beforeAll(() => {
  const u = URL as unknown as {
    createObjectURL: (b: Blob) => string
    revokeObjectURL: (url: string) => void
  }
  if (typeof u.createObjectURL !== 'function') u.createObjectURL = () => ''
  if (typeof u.revokeObjectURL !== 'function') u.revokeObjectURL = () => {}
})

afterEach(() => cleanup())

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'a1',
    name: 'bot',
    engine: 'claude-code',
    desired_state: 'running',
    actual_state: 'online',
    restart_policy: 'always',
    agents_md: null,
    ...overrides,
  }
}

function renderDialog(initialFiles: AgentFile[] = []) {
  const fetchAgentFiles = vi.fn().mockResolvedValue(initialFiles)
  const upsertAgentFile = vi
    .fn()
    .mockImplementation(async (_id: string, path: string, content: string) => ({
      path,
      content,
      updated_at: '2026-04-18T00:00:00Z',
    }))
  const deleteAgentFile = vi.fn().mockResolvedValue(undefined)
  const updateAgent = vi.fn().mockResolvedValue(makeAgent())
  render(
    <AgentEditDialog
      agent={makeAgent()}
      open={true}
      onOpenChange={() => {}}
      fetchAgentFiles={fetchAgentFiles}
      updateAgent={updateAgent}
      upsertAgentFile={upsertAgentFile}
      deleteAgentFile={deleteAgentFile}
    />,
  )
  return { fetchAgentFiles, upsertAgentFile, deleteAgentFile, updateAgent }
}

describe('AgentEditDialog — upload/download', () => {
  it('stages a UTF-8 file via the upload picker and fills the new-file form', async () => {
    renderDialog()
    // Dialog content mounts behind an async loadInitial; wait for
    // the Upload button before interacting.
    await screen.findByTestId('agent-edit-upload')

    const input = screen.getByTestId(
      'agent-edit-upload-input',
    ) as HTMLInputElement
    const file = new File(['hello world'], 'greet.md', {
      type: 'text/markdown',
    })
    fireEvent.change(input, { target: { files: [file] } })

    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    expect(pathInput.value).toBe('skills/greet.md')
    expect(screen.getByTestId('agent-edit-upload-badge')).toBeInTheDocument()
  })

  it('rejects a binary (non-UTF-8) file with an error', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-upload')

    const input = screen.getByTestId(
      'agent-edit-upload-input',
    ) as HTMLInputElement
    // Lone 0xff / 0xfe bytes — invalid UTF-8.
    const file = new File([new Uint8Array([0xff, 0xfe, 0x00, 0x80])], 'data.bin', {
      type: 'application/octet-stream',
    })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() =>
      expect(
        screen.getByText(/binary is not supported/i),
      ).toBeInTheDocument(),
    )
    // The confirmation form should not have opened because the
    // decode failed before setPendingContent/setShowNewFileForm.
    expect(screen.queryByTestId('agent-edit-new-file-path')).toBeNull()
  })

  it('rejects Add when the path extension is not in the server whitelist', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-upload')

    fireEvent.click(screen.getByTestId('agent-edit-toggle-new-file'))
    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: 'skills/do.sh' } })
    fireEvent.click(screen.getByText('Add'))

    expect(
      await screen.findByText(/extension must be one of/i),
    ).toBeInTheDocument()
  })

  it('Download builds a blob URL from the selected file content', async () => {
    renderDialog([
      {
        path: 'skills/greet/SKILL.md',
        content: 'hello',
        updated_at: '2026-04-18T00:00:00Z',
      },
    ])
    // Wait for the file row to render after fetchAgentFiles resolves.
    await screen.findByTestId('agent-edit-file-skills/greet/SKILL.md')

    const createSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:test')
    const revokeSpy = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => {})

    fireEvent.click(screen.getByTestId('agent-edit-download'))

    expect(createSpy).toHaveBeenCalledTimes(1)
    const blobArg = createSpy.mock.calls[0][0] as Blob
    await expect(blobArg.text()).resolves.toBe('hello')
    expect(revokeSpy).toHaveBeenCalledWith('blob:test')

    createSpy.mockRestore()
    revokeSpy.mockRestore()
  })
})
