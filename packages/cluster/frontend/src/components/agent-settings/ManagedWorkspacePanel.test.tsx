// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api'
import type { ManagedWorkspaceResult, WorkspaceEntry } from '@/hooks/useManagedWorkspace'
import ManagedWorkspacePanel from './ManagedWorkspacePanel'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
const fetchMock = vi.mocked(apiFetch)
const entry = (name: string, kind: WorkspaceEntry['kind'] = 'file'): WorkspaceEntry => ({ name, kind, size: null })
const result = (entries: WorkspaceEntry[] = [], path = ''): ManagedWorkspaceResult => ({
  status: 'ready', machine_id: 'machine', machine_name: 'Office machine', agent_state: 'running',
  snapshot: { status: 'ready', cwd: '/srv/agents/agent/workspace', engine: 'codex-cli', permission_level: 'standard', reported_at: '2026-09-26T12:00:00Z', runtime_generation: 1, live: true, path, entries, next_cursor: null, text: null, preview_status: null },
})
const response = (value: unknown, ok = true) => ({ ok, json: async () => value }) as Response
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
afterEach(() => { cleanup(); vi.resetAllMocks() })

it('browses response paths, opens read-only text and returns to a folder', async () => {
  fetchMock.mockImplementation(async path => {
    const url = new URL(path, 'http://fixture')
    const selected = url.searchParams.get('path')!
    if (url.pathname.endsWith('/file')) {
      const file = result([], selected); file.snapshot!.text = 'saved runtime output'; file.snapshot!.preview_status = 'text'
      return response(file)
    }
    return response(result(selected ? [entry('result.md')] : [entry('reports', 'directory'), entry('linked', 'link')], selected))
  })
  render(<ManagedWorkspacePanel agentId="agent" />)
  expect(await screen.findByText('/srv/agents/agent/workspace')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /linked/ })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'reports' }))
  fireEvent.click(await screen.findByRole('button', { name: 'result.md' }))
  expect(await screen.findByLabelText('Read-only file preview')).toHaveTextContent('saved runtime output')
  expect(fetchMock.mock.calls.some(([path]) => path.includes('/file?path=reports%2Fresult.md'))).toBe(true)
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Back to folder' }))
  expect(await screen.findByRole('button', { name: 'result.md' })).toBeInTheDocument()
})

it('distinguishes loading, failure, retry, empty and disconnected states', async () => {
  const pending = deferred<Response>()
  fetchMock.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response(result()))
  render(<ManagedWorkspacePanel agentId="agent" />)
  expect(screen.getByRole('status')).toHaveTextContent('Loading workspace files')
  expect(screen.queryByText('No visible files in this folder.')).not.toBeInTheDocument()
  await act(async () => { pending.resolve(response({}, false)) })
  expect(screen.getByRole('alert')).toHaveTextContent('Workspace files could not be loaded')
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByText('No visible files in this folder.')).toBeInTheDocument()
  fetchMock.mockResolvedValueOnce(response({ status: 'offline', machine_name: 'Office machine', machine_id: 'machine', agent_state: 'stopped', snapshot: null }))
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
  expect(await screen.findByText(/execution machine is disconnected/)).toBeInTheDocument()
  expect(screen.queryByText('No visible files in this folder.')).not.toBeInTheDocument()
})

it('discards late JSON across agent A to B to A', async () => {
  const old = deferred<ManagedWorkspaceResult>()
  fetchMock.mockResolvedValueOnce({ ok: true, json: () => old.promise } as Response)
    .mockResolvedValueOnce(response(result([entry('b-current')]))).mockResolvedValueOnce(response(result([entry('a-current')])))
  const view = render(<ManagedWorkspacePanel agentId="a" />)
  await act(async () => {})
  view.rerender(<ManagedWorkspacePanel agentId="b" />)
  expect(await screen.findByRole('button', { name: 'b-current' })).toBeInTheDocument()
  view.rerender(<ManagedWorkspacePanel agentId="a" />)
  expect(screen.queryByRole('button', { name: 'b-current' })).not.toBeInTheDocument()
  expect(await screen.findByRole('button', { name: 'a-current' })).toBeInTheDocument()
  await act(async () => { old.resolve(result([entry('obsolete')])) })
  expect(screen.queryByRole('button', { name: 'obsolete' })).not.toBeInTheDocument()
})

it('drops an old file response after selecting another file and bounds preview types', async () => {
  const old = deferred<ManagedWorkspaceResult>()
  const first = result([entry('old.txt'), entry('new.bin')])
  const binary = result(); binary.snapshot!.preview_status = 'binary'
  fetchMock.mockResolvedValueOnce(response(first)).mockResolvedValueOnce({ ok: true, json: () => old.promise } as Response)
    .mockResolvedValueOnce(response(first)).mockResolvedValueOnce(response(binary))
  render(<ManagedWorkspacePanel agentId="a" />)
  fireEvent.click(await screen.findByRole('button', { name: 'old.txt' }))
  await act(async () => {})
  fireEvent.click(screen.getByRole('button', { name: 'Back to folder' }))
  fireEvent.click(await screen.findByRole('button', { name: 'new.bin' }))
  expect(await screen.findByText(/not UTF-8 text/)).toBeInTheDocument()
  const obsolete = result(); obsolete.snapshot!.text = 'obsolete secret'; obsolete.snapshot!.preview_status = 'text'
  await act(async () => { old.resolve(obsolete) })
  expect(screen.queryByLabelText('Read-only file preview')).not.toBeInTheDocument()
})

it('loads additional directory pages without duplicate names', async () => {
  const first = result([entry('a.txt')]); first.snapshot!.next_cursor = 'a.txt'
  fetchMock.mockResolvedValueOnce(response(first)).mockResolvedValueOnce(response(result([entry('a.txt'), entry('b.txt')])))
  render(<ManagedWorkspacePanel agentId="a" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Load more files' }))
  expect(await screen.findByRole('button', { name: 'b.txt' })).toBeInTheDocument()
  expect(screen.getAllByRole('button', { name: 'a.txt' })).toHaveLength(1)
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more files' })).not.toBeInTheDocument())
})
