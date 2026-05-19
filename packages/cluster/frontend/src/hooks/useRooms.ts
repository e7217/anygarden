import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createElement } from 'react';
import { apiFetch } from '@/lib/api';

interface Project { id: string; name: string; description?: string; }

export interface Room {
  id: string;
  name: string;
  // #179 — ``null`` for DM rooms: they are no longer tied to any
  // project so a project-delete cannot cascade them away. Regular
  // rooms always carry a project id.
  project_id: string | null;
  is_dm: boolean;
  // Self-referential FK into the same table. ``null`` means top-
  // level room (directly under its project); a non-null value
  // means this is a sub-room of the referenced parent. The
  // server guarantees ``parent_room.project_id === project_id``
  // so the tree never crosses project boundaries — the sidebar
  // grouping-by-project still works.
  parent_room_id?: string | null;
  representative_agent_id?: string | null;
  participants?: unknown[];
  // Caller-specific sidebar pin state (#47). ``pinned`` promotes
  // the room to the sidebar's top pinned section; ``sort_order``
  // is a sparse integer used to order the pinned list. Server
  // populates both for registered users; guest sessions always
  // see ``pinned=false``.
  pinned?: boolean;
  sort_order?: number | null;
  // #237 — ephemeral mode. When True the agent is instructed (via
  // system_prompt) not to write to its long-term memory file in
  // this room. Trust-model signal, not a hard FS guard.
  ephemeral?: boolean;
  // Caller-specific sidebar update state (#385). True means this
  // user has not marked the room read at the latest message seq.
  has_updates?: boolean;
}

// Fetch state machine for the projects+rooms store.
//
//   idle     — nothing fetched yet (initial render, before the
//              provider's mount-time fetch kicks in).
//   loading  — a fetch for projects or any project's rooms is
//              in flight and the store is not yet consistent.
//   ready    — last fetch succeeded and the store is consistent
//              with the server.
//   error    — last fetch failed. Callers should show a retry
//              control before acting on the (possibly stale)
//              cached data.
//
// The Create Agent dialog keys off this status to distinguish
// "we know there are zero rooms" (``ready`` + empty) from "we
// don't know yet" (``loading`` / ``idle`` / ``error``). Without
// the distinction the dialog would show "No projects yet" during
// the initial fetch and let the admin create an agent with
// rooms=[] — landing straight in the pending trap.
export type RoomsStatus = 'idle' | 'loading' | 'ready' | 'error';

interface RoomsContextValue {
  projects: Project[];
  rooms: Record<string, Room[]>;
  agentDMs: Room[];
  status: RoomsStatus;
  fetchProjects: () => Promise<void>;
  fetchRooms: (projectId: string) => Promise<void>;
  fetchAgentDMs: () => Promise<void>;
  /** Run ``fetchProjects`` and then ``fetchRooms`` for every
   *  project in the fresh response. Use this when a dialog opens
   *  that needs the freshest view of the tree (e.g. the Create
   *  Agent room picker, or the Sub-room invitee list). */
  refetch: () => Promise<void>;
  createProject: (name: string) => Promise<Project>;
  /** Delete a project and every room it contains. The server
   *  cascades the DB delete and broadcasts ``RoomDeletedOut`` per
   *  child room; this local-state drop keeps the acting session's
   *  sidebar snappy without waiting on the WS round-trip. */
  deleteProject: (projectId: string) => Promise<void>;
  createRoom: (projectId: string, name: string) => Promise<Room>;
  createSubRoom: (
    parentRoomId: string,
    body: {
      name: string;
      participants: string[];
      creator_participant_id: string;
      is_dm?: boolean;
    },
    projectId: string,
  ) => Promise<Room>;
  /** Toggle sidebar pin state for a room (#47). Optimistic: local
   *  state updates immediately; failure rolls back and rethrows. */
  pinRoom: (roomId: string, pinned: boolean) => Promise<void>;
  /** Overwrite the pinned section order with a full snapshot (#47).
   *  Optimistic with rollback on failure. */
  reorderPinnedRooms: (roomIds: string[]) => Promise<void>;
  /** #237 — create a new DM room bound to ``agentId``. Returns the
   *  new room so callers can navigate to it. */
  createAgentDM: (agentId: string, name?: string) => Promise<Room>;
  /** #237 — PATCH the room's ephemeral flag. Optimistic update on
   *  local state so the room header reflects the change instantly. */
  setRoomEphemeral: (roomId: string, ephemeral: boolean) => Promise<void>;
  /** #385 — mark a room read for the current user and clear the
   *  sidebar update dot locally when the server accepts it. */
  markRoomRead: (roomId: string) => Promise<void>;
}

const RoomsContext = createContext<RoomsContextValue | null>(null);

/**
 * RoomsProvider — hosts the single source of truth for projects
 * and rooms state in the app.
 *
 * Before this existed, ``useRooms()`` was a plain hook and every
 * caller (``Sidebar``, ``ChatPage``, each admin page) got its own
 * independent copy of the state. That meant when ``ChatPage``
 * created a sub-room and refetched its own rooms map, ``Sidebar``
 * (which lives in the same render tree but called ``useRooms()``
 * separately) stayed stale until the user manually reloaded.
 *
 * The provider model keeps a single store so any caller can
 * trigger a refetch and every listener re-renders with the new
 * tree. Wrap the app in ``<RoomsProvider>`` at the top of
 * ``App.tsx`` and keep consumer components calling ``useRooms()``
 * — the call site stays identical.
 */
export function RoomsProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [rooms, setRooms] = useState<Record<string, Room[]>>({});
  const [agentDMs, setAgentDMs] = useState<Room[]>([]);
  const [status, setStatus] = useState<RoomsStatus>('idle');

  // ---- Silent fetchers --------------------------------------
  //
  // ``fetchProjects`` and ``fetchRooms`` are best-effort utilities
  // used as follow-up refreshes after mutations (createProject,
  // createRoom, createSubRoom, ChatPage.onCreated, etc.). They
  // MUST NOT throw and MUST NOT touch ``status``, because:
  //
  // 1. Some callers intentionally don't ``await`` or try/catch
  //    them (e.g. ChatPage's sub-room create callback). A throw
  //    here propagates as an unhandled promise rejection and
  //    breaks flows that already succeeded at the REST layer.
  //
  // 2. Touching ``status`` from here contaminates every other
  //    feature that reads it. A transient refresh blip in one
  //    dialog would lock the Create Agent button in a completely
  //    different dialog — a cross-feature failure mode that does
  //    not match what the admin is actually doing.
  //
  // Callers that need freshness guarantees (e.g. Create Agent
  // dialog) go through ``refetch()`` instead, which owns status
  // transitions and carries its own error handling.
  const fetchProjects = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/v1/projects');
      if (resp.ok) setProjects(await resp.json());
    } catch (e) {
      console.warn('RoomsProvider.fetchProjects failed (silent)', e);
    }
  }, []);

  const fetchRooms = useCallback(async (projectId: string) => {
    try {
      const resp = await apiFetch(`/api/v1/rooms?project_id=${projectId}&is_dm=false`);
      if (resp.ok) {
        const data = await resp.json();
        setRooms(prev => ({ ...prev, [projectId]: data }));
      }
    } catch (e) {
      console.warn(`RoomsProvider.fetchRooms(${projectId}) failed (silent)`, e);
    }
  }, []);

  const fetchAgentDMs = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/v1/rooms?is_dm=true');
      if (resp.ok) setAgentDMs(await resp.json());
    } catch (e) {
      console.warn('RoomsProvider.fetchAgentDMs failed (silent)', e);
    }
  }, []);

  const refreshSidebarRooms = useCallback(() => {
    if (localStorage.getItem('doorae_is_guest') === '1') return;
    projects.forEach(p => { void fetchRooms(p.id); });
    void fetchAgentDMs();
  }, [projects, fetchRooms, fetchAgentDMs]);

  // ---- Explicit refetch -------------------------------------
  //
  // ``refetch`` is the "I want the freshest possible tree right
  // now" escape hatch used by dialogs that open long after the
  // initial mount-time fetch (notably the Create Agent room
  // picker). Unlike the silent fetchers above, this path:
  //
  //   - flips ``status`` to ``loading`` before starting
  //   - fetches projects + every project's rooms in parallel
  //     via inlined requests (NOT via ``fetchRooms``) so a
  //     partial failure aborts the whole pass cleanly
  //   - resolves to ``ready`` on full success or ``error`` on
  //     any failure — callers key off ``status`` to show retry
  //     UI.
  //
  // It's intentionally self-contained: if ``fetchRooms``'s
  // silent implementation changes later, ``refetch`` isn't
  // affected.
  const refetch = useCallback(async () => {
    // Guest sessions don't have project-tree visibility (§11.5 of the
    // design doc — /projects and broad /rooms queries both 403 for a
    // guest JWT). Running the refetch anyway just floods the console
    // and parks ``status`` at ``error`` forever. No-op and stay on
    // ``idle`` so consumers treat it as "not applicable" rather than
    // "failed".
    if (localStorage.getItem('doorae_is_guest') === '1') return;
    setStatus('loading');
    try {
      const resp = await apiFetch('/api/v1/projects');
      if (!resp.ok) throw new Error(`GET /projects → ${resp.status}`);
      const freshProjects = (await resp.json()) as Project[];
      setProjects(freshProjects);
      // Fan out with Promise.all so slow single-project fetches
      // don't serialize the whole refresh.
      await Promise.all([
        // Fetch non-DM rooms per project
        ...freshProjects.map(async (p) => {
          const r = await apiFetch(`/api/v1/rooms?project_id=${p.id}&is_dm=false`);
          if (!r.ok) throw new Error(`GET /rooms?project_id=${p.id} → ${r.status}`);
          const data = (await r.json()) as Room[];
          setRooms(prev => ({ ...prev, [p.id]: data }));
        }),
        // Fetch agent DMs globally
        (async () => {
          const r = await apiFetch('/api/v1/rooms?is_dm=true');
          if (r.ok) setAgentDMs(await r.json());
        })(),
      ]);
      setStatus('ready');
    } catch (e) {
      console.error('RoomsProvider.refetch failed', e);
      setStatus('error');
    }
  }, []);

  // ---- Mutations --------------------------------------------
  //
  // Post-mutation refreshes go through the silent fetchers so
  // they never contaminate ``status``. The mutation itself
  // surfaces its own failure via throw — that's the caller's
  // feedback channel.

  const createProject = useCallback(async (name: string) => {
    const resp = await apiFetch('/api/v1/projects', {
      method: 'POST', body: JSON.stringify({ name }),
    });
    if (resp.ok) { await fetchProjects(); return await resp.json(); }
    throw new Error('Failed to create project');
  }, [fetchProjects]);

  const deleteProject = useCallback(async (projectId: string) => {
    const resp = await apiFetch(`/api/v1/projects/${projectId}`, {
      method: 'DELETE',
    });
    if (resp.status !== 204) {
      let detail = `Failed to delete project (${resp.status})`;
      try {
        const body = await resp.json();
        if (body && typeof body.detail === 'string') detail = body.detail;
      } catch { /* ignore body parse */ }
      throw new Error(detail);
    }
    // Drop the project and its rooms bucket from local state so the
    // sidebar updates within a frame. The per-room
    // ``room_deleted`` WS broadcasts will still arrive and be
    // harmlessly reconciled by the listener in ``useWebSocket``.
    setProjects(prev => prev.filter(p => p.id !== projectId));
    setRooms(prev => {
      const next = { ...prev };
      delete next[projectId];
      return next;
    });
  }, []);

  const createRoom = useCallback(async (projectId: string, name: string) => {
    const resp = await apiFetch('/api/v1/rooms', {
      method: 'POST', body: JSON.stringify({ project_id: projectId, name }),
    });
    if (resp.ok) { await fetchRooms(projectId); return await resp.json(); }
    throw new Error('Failed to create room');
  }, [fetchRooms]);

  // Create a child room under an existing room. The server
  // enforces that ``creator_participant_id`` must be a member of
  // the parent, and that every requested participant is also a
  // parent member — see
  // ``doorae-server/doorae/rooms/service.py::create_sub_room``.
  // The refetched list lets every subscriber (notably Sidebar)
  // pick up the new child immediately.
  const createSubRoom = useCallback(async (
    parentRoomId: string,
    body: {
      name: string;
      participants: string[];
      creator_participant_id: string;
      is_dm?: boolean;
    },
    projectId: string,
  ) => {
    const resp = await apiFetch(
      `/api/v1/rooms/${parentRoomId}/sub-rooms`,
      { method: 'POST', body: JSON.stringify(body) },
    );
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to create sub-room');
    }
    await fetchRooms(projectId);
    return await resp.json() as Room;
  }, [fetchRooms]);

  // ---- Pin / reorder (#47) ----------------------------------
  //
  // Both operations are optimistic: we mutate the local store
  // immediately so the sidebar responds within a frame, then send
  // the API call and roll back on failure. The server also pushes
  // ``room_pin_order_changed`` over WS so other tabs converge —
  // here we trust our own local update and let the WS listener
  // reconcile any race.

  const applyRoomPatch = useCallback(
    (roomId: string, patch: Partial<Room>) => {
      setRooms(prev => {
        const out: Record<string, Room[]> = { ...prev };
        for (const [pid, list] of Object.entries(prev)) {
          const idx = list.findIndex(r => r.id === roomId);
          if (idx !== -1) {
            const next = list.slice();
            next[idx] = { ...next[idx], ...patch };
            out[pid] = next;
          }
        }
        return out;
      });
    },
    [],
  );

  const pinRoom = useCallback(async (roomId: string, pinned: boolean) => {
    // Snapshot the before-state so rollback restores the exact row.
    let previous: { pinned?: boolean; sort_order?: number | null } | null = null;
    for (const list of Object.values(rooms)) {
      const hit = list.find(r => r.id === roomId);
      if (hit) {
        previous = { pinned: hit.pinned, sort_order: hit.sort_order };
        break;
      }
    }
    // Optimistic: pin=true goes to tail of the pinned section, pin=false clears.
    const nextSortOrder = pinned
      ? Math.max(
          0,
          ...Object.values(rooms)
            .flat()
            .filter(r => r.pinned)
            .map(r => r.sort_order ?? 0),
        ) + 1024
      : null;
    applyRoomPatch(roomId, { pinned, sort_order: nextSortOrder });

    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/pin`, {
        method: 'PATCH',
        body: JSON.stringify({ pinned }),
      });
      if (!resp.ok) throw new Error(`PATCH /rooms/${roomId}/pin → ${resp.status}`);
    } catch (e) {
      if (previous) applyRoomPatch(roomId, previous);
      throw e;
    }
  }, [rooms, applyRoomPatch]);

  const reorderPinnedRooms = useCallback(async (roomIds: string[]) => {
    // Snapshot sort_order for every affected room so rollback
    // restores the exact pre-drag state.
    const previous = new Map<string, number | null>();
    for (const list of Object.values(rooms)) {
      for (const r of list) {
        if (r.pinned && roomIds.includes(r.id)) {
          previous.set(r.id, r.sort_order ?? null);
        }
      }
    }
    // Optimistic: rewrite sort_order to match new order.
    setRooms(prev => {
      const out: Record<string, Room[]> = { ...prev };
      for (const [pid, list] of Object.entries(prev)) {
        out[pid] = list.map(r => {
          const idx = roomIds.indexOf(r.id);
          if (idx === -1 || !r.pinned) return r;
          return { ...r, sort_order: (idx + 1) * 1024 };
        });
      }
      return out;
    });

    try {
      const resp = await apiFetch('/api/v1/rooms/pin-order', {
        method: 'PUT',
        body: JSON.stringify({ room_ids: roomIds }),
      });
      if (!resp.ok) throw new Error(`PUT /rooms/pin-order → ${resp.status}`);
    } catch (e) {
      // Roll back every snapshotted row.
      setRooms(prev => {
        const out: Record<string, Room[]> = { ...prev };
        for (const [pid, list] of Object.entries(prev)) {
          out[pid] = list.map(r =>
            previous.has(r.id)
              ? { ...r, sort_order: previous.get(r.id) ?? null }
              : r,
          );
        }
        return out;
      });
      throw e;
    }
  }, [rooms]);

  // ---- #237 helpers ----------------------------------------
  //
  // ``createAgentDM`` mirrors ``createRoom`` but hits the
  // admin-only ``POST /api/v1/agents/{id}/dms`` endpoint that
  // seeds a new DM room for the agent. The caller navigates to
  // the returned room after refetching the DM list.
  //
  // ``setRoomEphemeral`` patches a room's ephemeral flag. We
  // update local state first so the header toggle renders
  // instantly, then roll back on failure. Server pushes a
  // ``room_settings_changed`` frame to other open sessions.

  const createAgentDM = useCallback(
    async (agentId: string, name?: string): Promise<Room> => {
      const resp = await apiFetch(`/api/v1/agents/${agentId}/dms`, {
        method: 'POST',
        body: JSON.stringify(name ? { name } : {}),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || 'Failed to create DM');
      }
      const room = (await resp.json()) as Room;
      await fetchAgentDMs();
      return room;
    },
    [fetchAgentDMs],
  );

  const setRoomEphemeral = useCallback(
    async (roomId: string, ephemeral: boolean) => {
      // Optimistic local patch across DMs + project rooms.
      setAgentDMs(prev =>
        prev.map(r => (r.id === roomId ? { ...r, ephemeral } : r)),
      );
      applyRoomPatch(roomId, { ephemeral });
      try {
        const resp = await apiFetch(`/api/v1/rooms/${roomId}`, {
          method: 'PATCH',
          body: JSON.stringify({ ephemeral }),
        });
        if (!resp.ok) throw new Error(`PATCH /rooms/${roomId} → ${resp.status}`);
      } catch (e) {
        // Roll back on failure.
        setAgentDMs(prev =>
          prev.map(r =>
            r.id === roomId ? { ...r, ephemeral: !ephemeral } : r,
          ),
        );
        applyRoomPatch(roomId, { ephemeral: !ephemeral });
        throw e;
      }
    },
    [applyRoomPatch],
  );

  const clearRoomUpdates = useCallback((roomId: string) => {
    setAgentDMs(prev =>
      prev.map(r => (r.id === roomId ? { ...r, has_updates: false } : r)),
    );
    setRooms(prev => {
      const out: Record<string, Room[]> = { ...prev };
      for (const [pid, list] of Object.entries(prev)) {
        out[pid] = list.map(r =>
          r.id === roomId ? { ...r, has_updates: false } : r,
        );
      }
      return out;
    });
  }, []);

  const markRoomRead = useCallback(async (roomId: string) => {
    if (localStorage.getItem('doorae_is_guest') === '1') return;
    try {
      const resp = await apiFetch(`/api/v1/rooms/${roomId}/read`, {
        method: 'POST',
      });
      if (resp.ok) clearRoomUpdates(roomId);
    } catch (e) {
      console.warn(`RoomsProvider.markRoomRead(${roomId}) failed (silent)`, e);
    }
  }, [clearRoomUpdates]);

  // ---- Boot cascade -----------------------------------------
  //
  // Initial mount runs ``refetch`` so ``status`` reaches
  // ``ready`` (or ``error``) without callers having to trigger
  // anything — this is what the Create Agent dialog keys off
  // on cold boot.
  useEffect(() => { void refetch(); }, [refetch]);

  // Whenever the projects list changes AFTER boot (e.g.
  // ``createProject`` just added one via the Sidebar), fan out
  // silent ``fetchRooms`` calls so the new project's rooms show
  // up in the tree. Without this cascade a freshly-created
  // project would appear in the sidebar with an empty rooms
  // list until something else triggered a refetch. The initial
  // boot doubles up briefly (refetch + this cascade both fire
  // once) but that's a tiny bandwidth cost for the correctness
  // guarantee.
  useEffect(() => {
    projects.forEach(p => { void fetchRooms(p.id); });
  }, [projects, fetchRooms]);

  // #385 — keep sidebar update dots reasonably fresh even when the
  // current tab is idle: poll once a minute and refresh immediately
  // when the user returns to a visible tab.
  useEffect(() => {
    const intervalId = window.setInterval(refreshSidebarRooms, 60_000);
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') refreshSidebarRooms();
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [refreshSidebarRooms]);

  // Server-pushed membership changes (see
  // ws/protocol.py::RoomMembershipChangedOut) arrive on whichever
  // per-room WS the user happens to have open. The room-level hook
  // re-emits them as ``doorae:rooms:invalidate`` window events so
  // we can refresh the tree from the provider regardless of which
  // ChatPage instance received the frame. ``refetch`` is the right
  // hammer here — DM additions, project additions, and per-project
  // room additions all need a consistent view.
  useEffect(() => {
    const handler = () => { void refetch(); };
    window.addEventListener('doorae:rooms:invalidate', handler);
    return () => window.removeEventListener('doorae:rooms:invalidate', handler);
  }, [refetch]);

  // Sidebar pin / reorder changes from another session of the same
  // user (#47). ``useWebSocket`` forwards the server's
  // ``room_pin_order_changed`` frame verbatim; we apply the new
  // order directly to local state — no refetch needed because the
  // frame carries the full pinned snapshot. Sparse integer spacing
  // (1024) matches the server so later local reorders stay in sync.
  useEffect(() => {
    const handler = (evt: Event) => {
      const detail = (evt as CustomEvent).detail as {
        pinned_room_ids?: string[];
      } | undefined;
      const pinnedIds = detail?.pinned_room_ids ?? [];
      const pinnedSet = new Set(pinnedIds);
      setRooms(prev => {
        const out: Record<string, Room[]> = {};
        for (const [pid, list] of Object.entries(prev)) {
          out[pid] = list.map(r => {
            if (pinnedSet.has(r.id)) {
              const idx = pinnedIds.indexOf(r.id);
              return { ...r, pinned: true, sort_order: (idx + 1) * 1024 };
            }
            // Rooms not in the snapshot are unpinned for this user.
            if (r.pinned) return { ...r, pinned: false, sort_order: null };
            return r;
          });
        }
        return out;
      });
    };
    window.addEventListener('doorae:rooms:pin-order', handler as EventListener);
    return () => {
      window.removeEventListener(
        'doorae:rooms:pin-order', handler as EventListener,
      );
    };
  }, []);

  // #237 — server-pushed ``room_settings_changed`` frame updates
  // our cached ``ephemeral`` flag so a toggle in another tab
  // propagates without a refetch. Guard on ``None`` → "not touched"
  // semantics (fields the server omits should leave local state
  // untouched).
  useEffect(() => {
    const handler = (evt: Event) => {
      const detail = (evt as CustomEvent).detail as {
        room_id?: string;
        ephemeral?: boolean | null;
      } | undefined;
      if (!detail?.room_id) return;
      if (detail.ephemeral === null || detail.ephemeral === undefined) return;
      const eph = detail.ephemeral;
      const roomId = detail.room_id;
      setAgentDMs(prev => prev.map(r => r.id === roomId ? { ...r, ephemeral: eph } : r));
      setRooms(prev => {
        const out: Record<string, Room[]> = { ...prev };
        for (const [pid, list] of Object.entries(prev)) {
          out[pid] = list.map(r => r.id === roomId ? { ...r, ephemeral: eph } : r);
        }
        return out;
      });
    };
    window.addEventListener(
      'doorae:rooms:settings-changed', handler as EventListener,
    );
    return () => window.removeEventListener(
      'doorae:rooms:settings-changed', handler as EventListener,
    );
  }, []);

  const value = useMemo<RoomsContextValue>(() => ({
    projects,
    rooms,
    agentDMs,
    status,
    fetchProjects,
    fetchRooms,
    fetchAgentDMs,
    refetch,
    createProject,
    deleteProject,
    createRoom,
    createSubRoom,
    pinRoom,
    reorderPinnedRooms,
    createAgentDM,
    setRoomEphemeral,
    markRoomRead,
  }), [projects, rooms, agentDMs, status, fetchProjects, fetchRooms, fetchAgentDMs, refetch, createProject, deleteProject, createRoom, createSubRoom, pinRoom, reorderPinnedRooms, createAgentDM, setRoomEphemeral, markRoomRead]);

  // JSX is deliberately avoided here to keep this file a ``.ts``
  // (not ``.tsx``) so the import shape of existing callers stays
  // unchanged. ``createElement`` is functionally identical.
  return createElement(RoomsContext.Provider, { value }, children);
}

export function useRooms(): RoomsContextValue {
  const ctx = useContext(RoomsContext);
  if (ctx === null) {
    throw new Error(
      'useRooms() must be called inside <RoomsProvider>. ' +
        'Wrap the app root in src/App.tsx.',
    );
  }
  return ctx;
}
