// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import type { ManagedWorkspaceResult } from '@/hooks/useManagedWorkspace'
import ManagedWorkspaceEditor from './ManagedWorkspaceEditor'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const response = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value }) as Response
const token = 'a'.repeat(64)
const sha = 'b'.repeat(64)
const folderData: ManagedWorkspaceResult = {
  status: 'ready', machine_id: 'machine', machine_name: 'Worker', agent_state: 'running', can_edit: true,
  snapshot: { status: 'ready', cwd: '/workspace', engine: 'codex-cli', permission_level: 'standard', reported_at: '2026-09-27T00:00:00Z', runtime_generation: 1, live: true, path: 'docs', entries: [], next_cursor: null, text: null, preview_status: null, edit_token: token },
}
const fileData: ManagedWorkspaceResult = {
  ...folderData, snapshot: { ...folderData.snapshot!, path: 'docs/note.md', text: 'original', preview_status: 'text', sha256: sha },
}
function view(filePath: string | null = null) {
  const reloadFolder = vi.fn(async () => {})
  const reloadFile = vi.fn(async () => {})
  render(<ManagedWorkspaceEditor agentId="agent-a" folder="docs" filePath={filePath}
    folderData={folderData} fileData={filePath ? fileData : null}
    reloadFolder={reloadFolder} reloadFile={reloadFile} />)
  return { reloadFolder, reloadFile }
}
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('creates a folder and a text file through the placed agent workspace API', async () => {
  fetchMock.mockResolvedValue(response({ status: 'ready' }))
  const { reloadFolder } = view()
  fireEvent.click(screen.getByRole('button', { name: 'New folder' }))
  fireEvent.change(screen.getByLabelText('Folder name'), { target: { value: 'reports' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create' }))
  await waitFor(() => expect(reloadFolder).toHaveBeenCalledTimes(1))
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agents/agent-a/workspace/folder', expect.objectContaining({ method: 'POST', body: JSON.stringify({ path: 'docs/reports', edit_token: token }) }))
  fireEvent.click(screen.getByRole('button', { name: 'New text file' }))
  fireEvent.change(screen.getByLabelText('File name'), { target: { value: 'notes.md' } })
  fireEvent.change(screen.getByLabelText('File contents'), { target: { value: 'hello' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create' }))
  await waitFor(() => expect(reloadFolder).toHaveBeenCalledTimes(2))
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agents/agent-a/workspace/file', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ path: 'docs/notes.md', text: 'hello', expected_sha256: 'absent', edit_token: token }) }))
})

it('keeps unsaved text after a conflict and sends the original revision', async () => {
  fetchMock.mockResolvedValueOnce(response({}, 409)).mockResolvedValueOnce(response({ status: 'ready' }))
  const { reloadFile } = view('docs/note.md')
  fireEvent.change(screen.getByLabelText('File contents'), { target: { value: 'edited draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('file or name changed')
  expect(screen.getByLabelText('File contents')).toHaveValue('edited draft')
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/agents/agent-a/workspace/file', expect.objectContaining({ body: JSON.stringify({ path: 'docs/note.md', text: 'edited draft', expected_sha256: sha, edit_token: token }) }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(reloadFile).toHaveBeenCalledWith('docs/note.md'))
})

it('uploads bytes without replacing an existing file and blocks oversized files locally', async () => {
  fetchMock.mockResolvedValue(response({ status: 'ready' }))
  const { reloadFolder } = view()
  fireEvent.change(screen.getByLabelText('Upload file'), { target: { files: [new File(['\0a'], 'data.bin')] } })
  await waitFor(() => expect(reloadFolder).toHaveBeenCalledTimes(1))
  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
  expect(body).toEqual({ path: 'docs/data.bin', content_base64: 'AGE=', edit_token: token })
  fireEvent.change(screen.getByLabelText('Upload file'), { target: { files: [new File([new Uint8Array(1024 * 1024 + 1)], 'large.bin')] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('1 MiB')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
