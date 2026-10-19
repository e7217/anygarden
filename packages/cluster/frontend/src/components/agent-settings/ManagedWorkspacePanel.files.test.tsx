// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import type { ManagedWorkspaceResult, WorkspaceEntry } from '@/hooks/useManagedWorkspace'
import ManagedWorkspacePanel from './ManagedWorkspacePanel'

// #908 — download, delete, rename and drag-and-drop upload of work files.
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const TOKEN = 'a'.repeat(64)
const entry = (name: string, kind: WorkspaceEntry['kind'] = 'file'): WorkspaceEntry => ({ name, kind, size: 3 })
const folder = (entries: WorkspaceEntry[], manage = true): ManagedWorkspaceResult => ({
  status: 'ready', machine_id: 'm', machine_name: 'Office machine', agent_state: 'running',
  can_edit: true, can_manage_files: manage,
  snapshot: { status: 'ready', cwd: '/srv/agents/agent', engine: 'codex-cli', permission_level: 'standard', reported_at: '2026-10-19T00:00:00Z', runtime_generation: 1, live: true, path: '', entries, next_cursor: null, text: null, preview_status: null, edit_token: TOKEN },
})
const json = (value: unknown, status = 200) => ({ ok: status < 400, status, json: async () => value, blob: async () => new Blob(['x']) }) as Response
afterEach(() => { cleanup(); vi.resetAllMocks() })

function serve(entries: () => WorkspaceEntry[], posts: Array<{ url: string; body: Record<string, string> }>, uploadStatus: () => number = () => 200) {
  fetchMock.mockImplementation(async (path, init) => {
    const url = new URL(path as string, 'http://fixture')
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      posts.push({ url: url.pathname, body })
      if (url.pathname.endsWith('/upload') && uploadStatus() === 409) return json({ detail: 'conflict' }, 409)
      return json(folder(entries()))
    }
    return json(folder(entries()))
  })
}

it('offers download, rename and delete for each file [WS-02]', async () => {
  const posts: Array<{ url: string; body: Record<string, string> }> = []
  serve(() => [entry('report.md'), entry('out', 'directory')], posts)
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<ManagedWorkspacePanel agentId="agent" />)
  expect(await screen.findByTestId('workspace-download-report.md')).toBeInTheDocument()
  expect(screen.queryByTestId('workspace-download-out')).not.toBeInTheDocument()

  fireEvent.click(screen.getByTestId('workspace-rename-report.md'))
  fireEvent.change(screen.getByTestId('workspace-rename-input'), { target: { value: 'final.md' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(posts).toContainEqual({ url: '/api/v1/agents/agent/workspace/rename', body: { path: 'report.md', new_name: 'final.md', edit_token: TOKEN } }))

  fireEvent.click(await screen.findByTestId('workspace-delete-out'))
  await waitFor(() => expect(posts).toContainEqual({ url: '/api/v1/agents/agent/workspace/delete', body: { path: 'out', edit_token: TOKEN } }))
})

it('hides file actions on a machine without the capability', async () => {
  fetchMock.mockResolvedValue(json(folder([entry('report.md')], false)))
  render(<ManagedWorkspacePanel agentId="agent" />)
  expect(await screen.findByTestId('workspace-manage-unavailable')).toBeInTheDocument()
  expect(screen.queryByTestId('workspace-download-report.md')).not.toBeInTheDocument()
})

it('uploads dropped files and asks before replacing one [WS-02]', async () => {
  const posts: Array<{ url: string; body: Record<string, string> }> = []
  let uploads = 0
  // The second file exists: its first upload attempt conflicts.
  serve(() => [entry('b.txt')], posts, () => (++uploads === 2 ? 409 : 200))
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(<ManagedWorkspacePanel agentId="agent" />)
  const list = await screen.findByTestId('workspace-file-list')
  const files = [new File(['A'], 'a.txt'), new File(['B'], 'b.txt'), new File(['x'.repeat(1024 * 1024 + 1)], 'big.bin')]
  fireEvent.dragEnter(list, { dataTransfer: { types: ['Files'], files } })
  expect(screen.getByTestId('workspace-drop-overlay')).toBeInTheDocument()
  fireEvent.drop(list, { dataTransfer: { types: ['Files'], files } })

  const results = await screen.findByTestId('workspace-upload-results')
  expect(confirmSpy).toHaveBeenCalledTimes(1)
  expect(results).toHaveTextContent('a.txtUploaded')
  expect(results).toHaveTextContent('b.txtUploaded')
  expect(results).toHaveTextContent('big.binNot uploaded')
  expect(posts.map(post => post.url.split('/').pop())).toEqual(['upload', 'upload', 'delete', 'upload'])
  expect(posts[2].body).toEqual({ path: 'b.txt', edit_token: TOKEN })
})

it('skips a conflicting file when replacing is declined', async () => {
  const posts: Array<{ url: string; body: Record<string, string> }> = []
  serve(() => [entry('b.txt')], posts, () => 409)
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  render(<ManagedWorkspacePanel agentId="agent" />)
  const list = await screen.findByTestId('workspace-file-list')
  fireEvent.drop(list, { dataTransfer: { types: ['Files'], files: [new File(['B'], 'b.txt')] } })
  expect(await screen.findByTestId('workspace-upload-results')).toHaveTextContent('b.txtSkipped')
  expect(posts.map(post => post.url.split('/').pop())).toEqual(['upload'])
})
