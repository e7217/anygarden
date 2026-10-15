import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'

/** Another server this node also serves as one of its machines (#848). */
export interface MachineLink {
  machine_id: string
  name: string
  server: string
  status: 'connecting' | 'connected' | 'disconnected' | 'auth_failed' | 'error' | 'stopped'
  error: string | null
  running_agents: number
}

export class MachineLinkError extends Error {
  constructor(message: string, readonly code?: string) {
    super(message)
  }
}

async function failure(resp: Response): Promise<MachineLinkError> {
  try {
    const body = await resp.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object') return new MachineLinkError(detail.message ?? '', detail.code)
  } catch { /* fall through */ }
  return new MachineLinkError('')
}

const POLL_MS = 5000

/**
 * ``available`` is false when this server is not an integrated node
 * (`anygarden start`): only a node can serve another server.
 */
export function useMachineLinks() {
  const [links, setLinks] = useState<MachineLink[]>([])
  const [available, setAvailable] = useState<boolean | null>(null)

  const refresh = useCallback(async () => {
    const resp = await apiFetch('/api/v1/machine-links')
    if (resp.status === 404 || resp.status === 403) {
      setAvailable(false)
      return
    }
    if (!resp.ok) throw await failure(resp)
    setLinks(await resp.json())
    setAvailable(true)
  }, [])

  useEffect(() => {
    let active = true
    const tick = () => { if (active) void refresh().catch(() => {}) }
    tick()
    const timer = window.setInterval(tick, POLL_MS)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])

  const addLink = useCallback(async (code: string) => {
    const resp = await apiFetch('/api/v1/machine-links', { method: 'POST', body: JSON.stringify({ code: code.trim() }) })
    if (!resp.ok) throw await failure(resp)
    const link: MachineLink = await resp.json()
    setLinks(prev => [...prev.filter(item => item.machine_id !== link.machine_id), link])
    return link
  }, [])

  const removeLink = useCallback(async (machineId: string) => {
    const resp = await apiFetch(`/api/v1/machine-links/${encodeURIComponent(machineId)}`, { method: 'DELETE' })
    if (!resp.ok && resp.status !== 404) throw await failure(resp)
    setLinks(prev => prev.filter(item => item.machine_id !== machineId))
  }, [])

  return { links, available, refresh, addLink, removeLink }
}
