import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { clearAuthSession, getAuthToken } from '@/lib/authStorage';
import { isAgentStage, peerProgressFrom, type AgentStage, type PeerProgress } from '@/lib/typingStage';

export interface ChatMessage {
  type: string; id: string; room_id: string;
  /** null if the original sender was removed from the room (FK SET NULL). */
  participant_id: string | null;
  content: string;
  parent_message_id?: string | null;
  root_message_id?: string | null;
  seq: number; created_at: string;
  metadata?: Record<string, unknown>;
}

interface RoomScope { roomId: string | null; active: boolean }
interface RoomState {
  scope: RoomScope;
  messages: ChatMessage[];
  connected: boolean;
  typingUsers: Set<string>;
  typingStages: Record<string, AgentStage>;
  typingProgress: Record<string, PeerProgress>;
  messageContextError: string | null;
}
const emptyRoomState = (scope: RoomScope): RoomState => ({
  scope, messages: [], connected: false, typingUsers: new Set(), typingStages: {}, typingProgress: {}, messageContextError: null,
});

export function useWebSocket(roomId: string | null, focusMessageId: string | null = null) {
  // The scope identifies a visit, including A → B → A, so callbacks from an
  // earlier visit cannot restore its messages, typing state or reconnect cursor.
  const scope = useMemo<RoomScope>(() => ({ roomId, active: true }), [roomId]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const isCurrent = useCallback(() => scope.active && currentScope.current === scope, [scope]);
  const [snapshot, setSnapshot] = useState<RoomState>(() => emptyRoomState(scope));
  const { messages, connected, typingUsers, typingStages, typingProgress, messageContextError } = snapshot.scope === scope
    ? snapshot : emptyRoomState(scope);
  const updateState = useCallback((patch: (previous: RoomState) => Partial<RoomState>) => {
    if (!isCurrent()) return;
    setSnapshot(previous => {
      if (!isCurrent()) return previous;
      const current = previous.scope === scope ? previous : emptyRoomState(scope);
      return { ...current, ...patch(current) };
    });
  }, [scope, isCurrent]);
  const wsRef = useRef<WebSocket | null>(null);
  const socketTokenRef = useRef<string | null>(null);
  const seqRef = useRef(0);
  const reconnectRef = useRef(1);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressReconnectRef = useRef(false);
  // #731 — set when the server closed us with 4040 ("superseded"): another
  // connection for the same participant took over. Retrying on a timer would
  // just knock that one off in turn, so we wait for the user to come back.
  const supersededRef = useRef(false);
  // Debounced typing expire timers — one per participant.
  // Each new typing=true RESETS the timer instead of stacking.
  const typingTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const clearTyping = useCallback(() => {
    Object.values(typingTimers.current).forEach(clearTimeout);
    typingTimers.current = {};
    updateState(() => ({ typingUsers: new Set(), typingStages: {}, typingProgress: {} }));
  }, [updateState]);

  const connect = useCallback(() => {
    if (!roomId || !isCurrent()) return;
    const token = getAuthToken();
    if (!token) return;

    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    let url = `${proto}//${host}/ws/rooms/${roomId}`;
    if (seqRef.current > 0) url += `?since_seq=${seqRef.current}`;

    supersededRef.current = false;
    const ws = new WebSocket(url, ['anygarden.v1', `bearer.${token}`]);
    wsRef.current = ws;
    socketTokenRef.current = token;
    const isCurrentSocket = () => isCurrent() && wsRef.current === ws && getAuthToken() === token;

    ws.onopen = () => {
      if (!isCurrentSocket()) return;
      updateState(() => ({ connected: true }));
      reconnectRef.current = 1;
    };
    ws.onclose = (evt) => {
      if (!isCurrentSocket()) return;
      updateState(() => ({ connected: false }));
      wsRef.current = null;
      socketTokenRef.current = null;
      clearTyping();

      const authRejected = evt.code === 4001 || evt.code === 4003;
      if (authRejected) {
        window.dispatchEvent(
          new CustomEvent('anygarden:auth:invalid', {
            detail: { code: evt.code, reason: evt.reason },
          }),
        );
      }

      if (evt.code === 4040) {
        supersededRef.current = true;
        return;
      }

      const currentToken = getAuthToken();
      if (
        authRejected
        || suppressReconnectRef.current
        || !currentToken
        || currentToken !== token
      ) return;

      const delay = Math.min(reconnectRef.current, 30);
      reconnectRef.current = Math.min(delay * 2, 30);
      reconnectTimerRef.current = setTimeout(() => {
        if (!isCurrent() || getAuthToken() !== token) return;
        reconnectTimerRef.current = null;
        connect();
      }, delay * 1000);
    };
    ws.onmessage = (evt) => {
      if (!isCurrentSocket()) return;
      const data = JSON.parse(evt.data);
      if (data.type === 'message') {
        if (data.room_id !== roomId) return;
        if (data.seq > seqRef.current) seqRef.current = data.seq;
        updateState(previous => {
          if (previous.messages.some(m => m.seq === data.seq)) return {};
          return { messages: [...previous.messages, data].sort((a, b) => a.seq - b.seq) };
        });
      } else if (data.type === 'room_membership_changed') {
        // Server pushes this when the user is added to (or removed
        // from) any room — see ws/protocol.py::RoomMembershipChangedOut.
        // The per-room useWebSocket hook can't directly touch the
        // RoomsProvider, so we re-emit on the window and let the
        // provider listen. Detail mirrors the server frame so future
        // consumers (toasts, focus-the-new-room flows) can read it
        // without parsing again.
        window.dispatchEvent(new CustomEvent('anygarden:rooms:invalidate', { detail: data }));
      } else if (data.type === 'room_pin_order_changed') {
        // Sidebar pin / reorder landed in another session of the
        // same user (#47). We forward it to the RoomsProvider via
        // a dedicated window event because the payload carries the
        // exact new order — the provider can apply it directly
        // without a refetch round-trip. Shape: { user_id, pinned_room_ids }.
        window.dispatchEvent(
          new CustomEvent('anygarden:rooms:pin-order', { detail: data }),
        )
      } else if (data.type === 'room_deleted') {
        // The whole room is gone. Bubble two events:
        //   1. ``anygarden:rooms:invalidate`` — same listener
        //      ``RoomsProvider`` already uses for membership changes,
        //      so the sidebar drops the room without us having to
        //      reach into the store.
        //   2. ``anygarden:room:deleted`` — carries the room_id so a
        //      page currently *viewing* that room can navigate
        //      away (otherwise the user is left staring at a 404
        //      or an empty chat view).
        window.dispatchEvent(new CustomEvent('anygarden:rooms:invalidate', { detail: data }));
        window.dispatchEvent(new CustomEvent('anygarden:room:deleted', { detail: data }));
      } else if (data.type === 'room_settings_changed') {
        // #237 — forward settings change so ``useRooms`` updates its
        // cached ephemeral flag on other tabs / other open sessions.
        window.dispatchEvent(
          new CustomEvent('anygarden:rooms:settings-changed', { detail: data }),
        );
      } else if (data.type === 'presence_update') {
        // #54 — participant liveness toggled in the current room.
        // The hook that actually tracks presence state
        // (``useParticipantPresence``) lives in the component tree
        // and can't receive this directly; we rebroadcast on
        // ``window`` the same way membership/pin-order events already
        // do. Detail mirrors the server frame exactly:
        //   { type, room_id, participant_id, online, last_seen_at }.
        window.dispatchEvent(
          new CustomEvent('anygarden:presence:update', { detail: data }),
        );
      } else if (data.type === 'task.updated') {
        // #266 — task lifecycle event. The 1차 view (TaskPanel) and
        // the 2차 view (AgentTasksTab) both subscribe via window
        // events because they live outside the per-room hook tree
        // (TaskPanel's filter state, AgentTasksTab's agent_id scope).
        // Detail shape: { type, event, task: {...} }.
        window.dispatchEvent(
          new CustomEvent('anygarden:task:updated', { detail: data }),
        );
      } else if (data.type === 'execution.updated') {
        window.dispatchEvent(new CustomEvent('anygarden:execution:updated', { detail: data }));
      } else if (data.type === 'room_artifact.added') {
        // #290 — agent dropped a new file in memory/outbox/. The
        // RoomArtifactsDialog (and any future right-rail panel)
        // listens on the window so it doesn't need to be wired into
        // the WS hook's prop tree. Detail mirrors the server frame.
        window.dispatchEvent(
          new CustomEvent('anygarden:room_artifact:added', { detail: data }),
        );
      } else if (data.type === 'room_artifact.removed') {
        window.dispatchEvent(
          new CustomEvent('anygarden:room_artifact:removed', { detail: data }),
        );
      } else if (data.type === 'typing') {
        const pid = data.participant_id;
        const forget = () => {
          updateState(previous => {
            const typingUsers = new Set(previous.typingUsers); typingUsers.delete(pid);
            const typingStages = { ...previous.typingStages }; delete typingStages[pid];
            const typingProgress = { ...previous.typingProgress }; delete typingProgress[pid];
            return { typingUsers, typingStages, typingProgress };
          });
        };
        if (data.is_typing) {
          const progress = data.stage === 'waiting_peers' ? peerProgressFrom(data) : null;
          updateState(previous => {
            const typingStages = { ...previous.typingStages };
            if (isAgentStage(data.stage)) typingStages[pid] = data.stage;
            else delete typingStages[pid];
            const typingProgress = { ...previous.typingProgress };
            if (progress) typingProgress[pid] = progress;
            else delete typingProgress[pid];
            return { typingUsers: new Set(previous.typingUsers).add(pid), typingStages, typingProgress };
          });
          // Reset the expire timer — don't stack multiple timeouts
          if (typingTimers.current[pid]) clearTimeout(typingTimers.current[pid]);
          typingTimers.current[pid] = setTimeout(() => {
            if (!isCurrentSocket()) return;
            forget();
            delete typingTimers.current[pid];
          }, 5000);
        } else {
          if (typingTimers.current[pid]) {
            clearTimeout(typingTimers.current[pid]);
            delete typingTimers.current[pid];
          }
          forget();
        }
      }
    };
  }, [roomId, isCurrent, updateState, clearTyping]);

  useEffect(() => {
    scope.active = true;
    updateState(() => ({ messages: [], connected: false }));
    clearTyping();
    seqRef.current = 0;
    suppressReconnectRef.current = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
    connect();
    return () => {
      scope.active = false;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); wsRef.current = null; }
      socketTokenRef.current = null;
      clearTyping();
    };
  }, [scope, updateState, connect, clearTyping]);

  // Reclaim a superseded room connection when this tab is back in front of
  // the user — the most recently used tab ends up holding the connection.
  useEffect(() => {
    const revive = () => {
      if (!supersededRef.current || document.visibilityState === 'hidden') return;
      connect();
    };
    document.addEventListener('visibilitychange', revive);
    window.addEventListener('focus', revive);
    return () => {
      document.removeEventListener('visibilitychange', revive);
      window.removeEventListener('focus', revive);
    };
  }, [connect]);

  const send = useCallback((
    content: string,
    metadata?: Record<string, unknown>,
    threadRootId?: string,
  ) => {
    if (!isCurrent() || socketTokenRef.current !== getAuthToken()) return;
    const frame: Record<string, unknown> = { type: 'send', content }
    if (metadata && Object.keys(metadata).length > 0) frame.metadata = metadata
    if (threadRootId) frame.thread_root_id = threadRootId
    wsRef.current?.send(JSON.stringify(frame));
  }, [isCurrent]);

  const sendTyping = useCallback((isTyping: boolean) => {
    if (!isCurrent() || socketTokenRef.current !== getAuthToken()) return;
    wsRef.current?.send(JSON.stringify({ type: 'typing', is_typing: isTyping }));
  }, [isCurrent]);

  // Load history via REST on mount
  useEffect(() => {
    if (!roomId) return;
    const token = getAuthToken();
    if (!token) return;
    const controller = new AbortController();
    const accepts = () => isCurrent() && !controller.signal.aborted && getAuthToken() === token;
    fetch(`/api/v1/rooms/${roomId}/messages?since_seq=0&limit=100`, {
      headers: { 'Authorization': `Bearer ${token}` },
      signal: controller.signal,
    }).then(r => {
      if (!accepts()) return [];
      if (r.status === 401 || r.status === 403) {
        if (getAuthToken() === token) {
          suppressReconnectRef.current = true;
          if (reconnectTimerRef.current) {
            clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
          }
          if (r.status === 401) {
            clearAuthSession();
          }
          window.dispatchEvent(
            new CustomEvent('anygarden:auth:invalid', {
              detail: { status: r.status },
            }),
          );
        }
        return [];
      }
      return r.ok ? r.json() : [];
    }).then(msgs => {
      if (accepts() && Array.isArray(msgs) && msgs.length) {
        const history = (msgs as ChatMessage[]).filter(message => message.room_id === roomId);
        // History may finish after newer live messages. Preserve the live copy
        // of each sequence and never move the reconnect cursor backwards.
        seqRef.current = Math.max(seqRef.current, ...history.map(message => message.seq));
        updateState(previous => {
          const bySequence = new Map(history.map(message => [message.seq, message]));
          previous.messages.forEach(message => bySequence.set(message.seq, message));
          return { messages: [...bySequence.values()].sort((a, b) => a.seq - b.seq) };
        });
      }
    }).catch(() => {});
    return () => controller.abort();
  }, [roomId, isCurrent, updateState]);

  useEffect(() => {
    updateState(() => ({ messageContextError: null }));
    if (!roomId || !focusMessageId) return;
    const token = getAuthToken();
    if (!token) return;
    const controller = new AbortController();
    const accepts = () => isCurrent() && !controller.signal.aborted && getAuthToken() === token;
    fetch(`/api/v1/rooms/${roomId}/messages/${encodeURIComponent(focusMessageId)}/context`, {
      headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
    }).then(async response => {
      if (!accepts()) return;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const rows = await response.json() as ChatMessage[];
      if (!accepts() || !Array.isArray(rows)) return;
      const context = rows.filter(message => message.room_id === roomId);
      if (!context.some(message => message.id === focusMessageId)) throw new Error('HTTP 404');
      updateState(previous => {
        const bySequence = new Map(context.map(message => [message.seq, message]));
        previous.messages.forEach(message => bySequence.set(message.seq, message));
        return { messages: [...bySequence.values()].sort((a, b) => a.seq - b.seq) };
      });
    }).catch(error => {
      if (accepts()) updateState(() => ({ messageContextError: error instanceof Error ? error.message : 'HTTP 500' }));
    });
    return () => controller.abort();
  }, [roomId, focusMessageId, isCurrent, updateState]);

  return { messages, connected, typingUsers, typingStages, typingProgress, send, sendTyping, messageContextError };
}
