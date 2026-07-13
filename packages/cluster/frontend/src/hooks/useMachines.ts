import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '@/lib/api';

export interface Machine {
  id: string;
  name: string;
  // Daemon-detected real hostname (issue #523); empty until first connect.
  hostname: string;
  // User-supplied free-form label / note (issue #523).
  description?: string;
  status: string;
  daemon_version?: string;
  labels?: Record<string, string>;
  // Static system info reported by the daemon on register (issue #523).
  lan_ip?: string | null;
  os_platform?: string | null;
  cpu_cores?: number;
  memory_gb?: number;
}

export interface RegisterMachineResult {
  id: string;
  machine_token: string;
  name: string;
  hostname: string;
  description?: string;
  /**
   * Present only when the POST itself succeeded (machine was created
   * and ``machine_token`` is valid) but the follow-up list refresh
   * failed. Consumers MUST still surface the token to the operator —
   * the token is one-time and dropping it would strand them — and
   * should additionally display this warning so they know the
   * Machines table on screen may be stale.
   */
  refreshWarning?: string;
}

export type MachinesStatus = 'idle' | 'loading' | 'loaded' | 'error';

export function useMachines() {
  const [machines, setMachines] = useState<Machine[]>([]);
  // 'idle' → fetch hasn't started; 'loading' → in flight; 'loaded' →
  // last fetch succeeded; 'error' → last fetch failed. Consumers can
  // use this to distinguish "machines list is empty" from "we don't
  // know yet" when deciding how to render machine references.
  const [status, setStatus] = useState<MachinesStatus>('idle');
  // Ref mirror of status so mutation callbacks can read the current
  // value without becoming dependents (and re-creating on every fetch
  // transition). Kept in sync via effect below.
  const statusRef = useRef<MachinesStatus>('idle');
  useEffect(() => { statusRef.current = status; }, [status]);

  const fetchMachines = useCallback(async () => {
    setStatus('loading');
    try {
      const resp = await apiFetch('/api/v1/machines');
      if (!resp.ok) {
        setStatus('error');
        throw new Error(`Failed to fetch machines: ${resp.status}`);
      }
      const data: Machine[] = await resp.json();
      data.sort((a, b) => {
        if (a.status === 'online' && b.status !== 'online') return -1;
        if (a.status !== 'online' && b.status === 'online') return 1;
        return a.name.localeCompare(b.name);
      });
      setMachines(data);
      setStatus('loaded');
    } catch (err) {
      setStatus('error');
      throw err;
    }
  }, []);

  // Fire-and-forget refresh — used after mutations to pick up any
  // server-side side effects we haven't already patched into local
  // state. We swallow errors here because the mutation itself already
  // updated the local machines list optimistically; status stays
  // 'error' so operators can tell the last refresh drifted.
  const refreshInBackground = useCallback(() => {
    fetchMachines().catch(() => { /* status already set to 'error' */ });
  }, [fetchMachines]);

  const drainMachine = useCallback(async (id: string) => {
    const resp = await apiFetch(`/api/v1/machines/${id}/drain`, { method: 'POST' });
    if (!resp.ok) throw new Error('Failed to drain machine');
    const body = await resp.json();
    // Server returns {id, status: "draining"}. Patch local state so
    // the UI reflects the change even if the follow-up refresh fails.
    setMachines(prev =>
      prev.map(m => (m.id === id ? { ...m, status: body.status ?? 'draining' } : m))
    );
    refreshInBackground();
  }, [refreshInBackground]);

  const registerMachine = useCallback(async (data: { name: string; description?: string }): Promise<RegisterMachineResult> => {
    const resp = await apiFetch('/api/v1/machines', { method: 'POST', body: JSON.stringify(data) });
    if (!resp.ok) throw new Error('Failed to register machine');
    const result: RegisterMachineResult & {
      status?: string;
      daemon_version?: string;
      labels?: Record<string, string>;
    } = await resp.json();

    // CRITICAL: the server-side machine has been created and `result`
    // already contains the one-time machine_token. From this point on
    // we must NEVER throw, because losing the token would strand the
    // operator — the token is only ever returned once. Any list-sync
    // failure becomes a `refreshWarning` attached to the result so
    // the caller can still show the token dialog.
    try {
      if (statusRef.current === 'loaded') {
        // Local list is already authoritative → safe to optimistic-
        // append and kick off a background refresh.
        setMachines(prev => [
          ...prev,
          {
            id: result.id,
            name: result.name,
            hostname: result.hostname,
            description: result.description,
            status: result.status ?? 'offline',
            daemon_version: result.daemon_version,
            labels: result.labels,
          },
        ]);
        refreshInBackground();
      } else {
        // Local list wasn't authoritative (idle/loading/error). Force
        // a real fetch so we don't leave a one-row partial list
        // pretending to be complete.
        await fetchMachines();
      }
      return result;
    } catch (e) {
      return {
        ...result,
        refreshWarning:
          (e as Error).message ||
          'Machine was created but the machines list could not be refreshed.',
      };
    }
  }, [fetchMachines, refreshInBackground]);

  const updateMachine = useCallback(async (id: string, data: { name?: string; description?: string; labels?: Record<string, string> }) => {
    const resp = await apiFetch(`/api/v1/machines/${id}`, { method: 'PATCH', body: JSON.stringify(data) });
    if (!resp.ok) throw new Error('Failed to update machine');
    const updated: Machine = await resp.json();
    // Merge the server's canonical shape into local state so the cell
    // reflects the change even if the background refresh fails.
    setMachines(prev => prev.map(m => (m.id === id ? { ...m, ...updated } : m)));
    refreshInBackground();
    return updated;
  }, [refreshInBackground]);

  const deleteMachine = useCallback(async (id: string, force: boolean = false): Promise<{ stopped_agents: string[] }> => {
    const url = force ? `/api/v1/machines/${id}?force=true` : `/api/v1/machines/${id}`;
    const resp = await apiFetch(url, { method: 'DELETE' });
    if (resp.status === 409) {
      const body = await resp.json();
      const detail = body.detail || {};
      const err = new Error(detail.message || 'Machine has active agents') as Error & { code?: string; agentCount?: number };
      err.code = detail.error;
      err.agentCount = detail.agent_count;
      throw err;
    }
    if (!resp.ok) throw new Error('Failed to delete machine');
    const result = await resp.json();
    // Remove from local list immediately so the row vanishes even if
    // the refresh fails.
    setMachines(prev => prev.filter(m => m.id !== id));
    refreshInBackground();
    return result;
  }, [refreshInBackground]);

  const regenerateToken = useCallback(async (id: string, revokeOnly: boolean = false): Promise<{ token: string; pushed: boolean; mode: string }> => {
    const url = revokeOnly
      ? `/api/v1/machines/${id}/tokens/regenerate?revoke_only=true`
      : `/api/v1/machines/${id}/tokens/regenerate`;
    const resp = await apiFetch(url, { method: 'POST' });
    if (resp.ok) {
      const result = await resp.json();
      return {
        token: result.machine_token,
        pushed: result.pushed_to_daemon,
        mode: result.mode,
      };
    }
    throw new Error('Failed to regenerate token');
  }, []);

  // Initial load — fire-and-forget because mutation-initiated fetches
  // are the ones that need to propagate failures to their caller.
  useEffect(() => {
    fetchMachines().catch(() => { /* status already set to 'error' */ });
  }, [fetchMachines]);
  return {
    machines,
    status,
    drainMachine,
    registerMachine,
    updateMachine,
    deleteMachine,
    regenerateToken,
    fetchMachines,
  };
}
