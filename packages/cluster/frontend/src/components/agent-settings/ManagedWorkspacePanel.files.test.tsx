// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

describe('large transfers (#914)', () => {
  const transferFolder = (entries: WorkspaceEntry[]) => ({
    ...folder(entries), can_transfer: true, transfer_max_bytes: 20 * 1024 * 1024, archive_max_bytes: 200 * 1024 * 1024,
  })

  class FakeXhr {
    static sent: Array<{ method: string; url: string; body: unknown; headers: Record<string, string> }> = []
    status = 200
    upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null } = { onprogress: null }
    onload: (() => void) | null = null
    onerror: (() => void) | null = null
    private method = ''
    private url = ''
    private headers: Record<string, string> = {}
    open(method: string, url: string) { this.method = method; this.url = url }
    setRequestHeader(name: string, value: string) { this.headers[name] = value }
    send(body: unknown) {
      FakeXhr.sent.push({ method: this.method, url: this.url, body, headers: this.headers })
      this.upload.onprogress?.({ loaded: 5, total: 10, lengthComputable: true })
      setTimeout(() => this.onload?.(), 0)
    }
  }

  it('streams a file larger than 1 MiB with progress [WS-07]', async () => {
    FakeXhr.sent = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    fetchMock.mockResolvedValue(json(transferFolder([])))
    render(<ManagedWorkspacePanel agentId="agent" />)
    const list = await screen.findByTestId('workspace-file-list')
    const big = new File([new Uint8Array(2 * 1024 * 1024)], 'big.bin')
    fireEvent.drop(list, { dataTransfer: { types: ['Files'], files: [big] } })
    expect(await screen.findByTestId('workspace-upload-results')).toHaveTextContent('big.binUploaded')
    expect(FakeXhr.sent).toHaveLength(1)
    expect(FakeXhr.sent[0].method).toBe('PUT')
    expect(FakeXhr.sent[0].url).toBe(`/api/v1/agents/agent/workspace/upload-stream?path=big.bin&edit_token=${TOKEN}`)
    expect(FakeXhr.sent[0].body).toBe(big)
    vi.unstubAllGlobals()
  })

  it('downloads a folder as zip [WS-07]', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:zip')
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() })
    fetchMock.mockImplementation(async (path: string) => {
      if (path.includes('/archive')) return { ok: true, status: 200, headers: new Headers({ 'content-type': 'application/zip' }), body: null, blob: async () => new Blob(['zip']) } as unknown as Response
      return json(transferFolder([entry('out', 'directory')]))
    })
    render(<ManagedWorkspacePanel agentId="agent" />)
    fireEvent.click(await screen.findByTestId('workspace-zip-out'))
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/v1/agents/agent/workspace/archive?path=out')).toBe(true))
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled())
    fireEvent.click(screen.getByTestId('workspace-download-folder-zip'))
    await waitFor(() => expect(fetchMock.mock.calls.some(([path]) => String(path) === '/api/v1/agents/agent/workspace/archive?path=')).toBe(true))
  })

  it('tells an older machine to update for large files', async () => {
    fetchMock.mockResolvedValue(json(folder([entry('out', 'directory')])))
    render(<ManagedWorkspacePanel agentId="agent" />)
    expect(await screen.findByTestId('workspace-transfer-unavailable')).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-zip-out')).not.toBeInTheDocument()
    expect(screen.queryByTestId('workspace-download-folder-zip')).not.toBeInTheDocument()
  })
})
