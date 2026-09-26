import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '@/lib/api'

export interface MachineAgent {
  id: string; name: string; engine: string
  desired_state: string; actual_state: string
  reasoning_effort?: string | null; rooms: string[]
  avatar_kind?: string | null
  avatar_value?: string | null
  context_window_opt_out?: boolean
}

export interface MachineEngineInfo {
  engine: string
  version?: string | null
  latest_version?: string | null
  update_available?: boolean
  update_status?: string | null
  latest_checked_at?: string | null
}

interface MachineDetail {
  agents: MachineAgent[]
  engines: MachineEngineInfo[]
  activity: { id: string; event_type: string; timestamp: string; details: Record<string, unknown> | null }[]
}

type DetailStatus = 'idle' | 'loading' | 'loaded' | 'error'
interface DetailState {
  machineId: string | null
  status: DetailStatus
  data: MachineDetail | null
}

/** A detail snapshot always belongs to one machine and one completed request. */
export function useMachineDetail(machineId: string | null, refreshKey: string) {
  const currentId = useRef(machineId)
  currentId.current = machineId
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const [state, setState] = useState<DetailState>({ machineId: null, status: 'idle', data: null })

  const refresh = useCallback(async (id = currentId.current) => {
    // Mutation callbacks and delayed refreshes may still refer to a prior selection.
    if (!id || id !== currentId.current) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    const version = ++generation.current
    setState(previous => previous.machineId === id && previous.status === 'loaded'
      ? previous
      : { machineId: id, status: 'loading', data: null })
    const isCurrent = () => !request.signal.aborted && generation.current === version && currentId.current === id
    try {
      const responses = await Promise.all([
        apiFetch(`/api/v1/machines/${id}/agents`, { signal: request.signal }),
        apiFetch(`/api/v1/machines/${id}/engines`, { signal: request.signal }),
        apiFetch(`/api/v1/machines/${id}/activity?limit=50`, { signal: request.signal }),
      ])
      if (responses.some(response => !response.ok)) throw new Error('Machine detail unavailable')
      const [agents, engines, activity] = await Promise.all(responses.map(response => response.json()))
      if (isCurrent()) setState({ machineId: id, status: 'loaded', data: { agents, engines, activity } })
    } catch {
      if (isCurrent()) setState({ machineId: id, status: 'error', data: null })
    }
  }, [])

  useEffect(() => {
    currentId.current = machineId
    void refresh(machineId)
    return () => {
      currentId.current = null
      ++generation.current
      controller.current?.abort()
    }
  }, [machineId, refreshKey, refresh])

  // A render after selection changes must not expose the old snapshot, even
  // before the effect has started the next request.
  const current = state.machineId === machineId ? state : null
  return {
    data: machineId ? current?.data ?? null : null,
    status: (machineId ? current?.status ?? 'loading' : 'idle') as DetailStatus,
    refresh,
  }
}
