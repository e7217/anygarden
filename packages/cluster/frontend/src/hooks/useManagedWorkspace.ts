import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'

export interface WorkspaceEntry {
  name: string
  kind: 'directory' | 'file' | 'link' | 'unavailable'
  size: number | null
}
export interface ManagedWorkspaceResult {
  status: string
  machine_id: string | null
  machine_name: string | null
  agent_state: string
  snapshot: null | {
    status: string
    cwd: string | null
    engine: string | null
    permission_level: string | null
    reported_at: string | null
    runtime_generation: number | null
    live: boolean
    path: string
    entries: WorkspaceEntry[]
    next_cursor: string | null
    text: string | null
    preview_status: 'text' | 'binary' | 'too_large' | null
  }
}
interface Query { kind: 'directory' | 'file'; path: string; cursor?: string }
interface State {
  agentId: string | null
  folder: string
  folderData: ManagedWorkspaceResult | null
  filePath: string | null
  fileData: ManagedWorkspaceResult | null
  pending: Query | null
  failed: Query | null
}
const initial = (agentId: string | null): State => ({ agentId, folder: '', folderData: null, filePath: null, fileData: null, pending: null, failed: null })

export function useManagedWorkspace(agentId: string | null) {
  const [state, setState] = useState<State>(() => initial(agentId))
  const current = useRef(state)
  const context = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const mounted = useRef(false)
  const publish = useCallback((next: State) => { current.current = next; setState(next) }, [])
  const load = useCallback(async (query: Query) => {
    if (!agentId || !mounted.current || current.current.agentId !== agentId) return
    const generation = ++context.current
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const valid = () => mounted.current && context.current === generation && !abort.signal.aborted
    const previous = current.current
    publish({ ...previous, pending: query, failed: null,
      ...(query.kind === 'directory'
        ? { folder: query.path, folderData: query.cursor ? previous.folderData : null, filePath: null, fileData: null }
        : { filePath: query.path, fileData: null }),
    })
    try {
      const params = new URLSearchParams({ path: query.path })
      if (query.cursor) params.set('cursor', query.cursor)
      const response = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}/workspace${query.kind === 'file' ? '/file' : ''}?${params}`, { signal: abort.signal })
      if (!valid()) return
      if (!response.ok) throw new Error('workspace request failed')
      const body = await response.json() as ManagedWorkspaceResult
      if (!valid()) return
      if (!body || typeof body.status !== 'string') throw new Error('invalid workspace response')
      if (query.kind === 'directory' && query.cursor && body.snapshot && current.current.folderData?.snapshot) {
        const existing = current.current.folderData.snapshot
        if (existing.cwd !== body.snapshot.cwd || existing.runtime_generation !== body.snapshot.runtime_generation || current.current.folderData.machine_id !== body.machine_id) throw new Error('workspace changed')
        const entries = new Map(existing.entries.map(entry => [entry.name, entry]))
        for (const entry of body.snapshot.entries) entries.set(entry.name, entry)
        body.snapshot.entries = [...entries.values()]
      }
      publish({ ...current.current, pending: null, failed: null,
        ...(query.kind === 'directory' ? { folderData: body } : { fileData: body }),
      })
    } catch {
      if (valid()) publish({ ...current.current, pending: null, failed: query })
    }
  }, [agentId, publish])

  useEffect(() => {
    mounted.current = true
    context.current += 1
    publish(initial(agentId))
    if (agentId) void load({ kind: 'directory', path: '' })
    return () => { mounted.current = false; context.current += 1; controller.current?.abort() }
  }, [agentId, load, publish])

  const value = state.agentId === agentId ? state : initial(agentId)
  return {
    ...value,
    loading: Boolean(value.pending),
    openFolder: (path: string) => load({ kind: 'directory', path }),
    openFile: (path: string) => load({ kind: 'file', path }),
    refresh: () => load(value.filePath !== null ? { kind: 'file', path: value.filePath } : { kind: 'directory', path: value.folder }),
    retry: () => load(value.failed ?? { kind: 'directory', path: value.folder }),
    loadMore: () => value.folderData?.snapshot?.next_cursor ? load({ kind: 'directory', path: value.folder, cursor: value.folderData.snapshot.next_cursor }) : Promise.resolve(),
  }
}
