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
  project_id: string;
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
    createRoom,
    createSubRoom,
  }), [projects, rooms, agentDMs, status, fetchProjects, fetchRooms, fetchAgentDMs, refetch, createProject, createRoom, createSubRoom]);

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
