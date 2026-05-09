import { useState, useRef, useCallback, useEffect } from 'react';
import { clearAuthSession, getAuthToken } from '@/lib/authStorage';

export interface ChatMessage {
  type: string; id: string; room_id: string;
  /** null if the original sender was removed from the room (FK SET NULL). */
  participant_id: string | null;
  content: string; seq: number; created_at: string;
  metadata?: Record<string, unknown>;
}

export function useWebSocket(roomId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const [typingUsers, setTypingUsers] = useState<Set<string>>(new Set());
  const wsRef = useRef<WebSocket | null>(null);
  const seqRef = useRef(0);
  const reconnectRef = useRef(1);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressReconnectRef = useRef(false);
  // Debounced typing expire timers — one per participant.
  // Each new typing=true RESETS the timer instead of stacking.
  const typingTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const connect = useCallback(() => {
    if (!roomId) return;
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

    const ws = new WebSocket(url, ['doorae.v1', `bearer.${token}`]);
    wsRef.current = ws;

    ws.onopen = () => { setConnected(true); reconnectRef.current = 1; };
    ws.onclose = (evt) => {
      setConnected(false);
      if (wsRef.current === ws) wsRef.current = null;

      const authRejected = evt.code === 4001 || evt.code === 4003;
      if (authRejected) {
        window.dispatchEvent(
          new CustomEvent('doorae:auth:invalid', {
            detail: { code: evt.code, reason: evt.reason },
          }),
        );
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
        reconnectTimerRef.current = null;
        connect();
      }, delay * 1000);
    };
    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === 'message') {
        if (data.seq > seqRef.current) seqRef.current = data.seq;
        setMessages(prev => {
          if (prev.some(m => m.seq === data.seq)) return prev;
          return [...prev, data];
        });
      } else if (data.type === 'room_membership_changed') {
        // Server pushes this when the user is added to (or removed
        // from) any room — see ws/protocol.py::RoomMembershipChangedOut.
        // The per-room useWebSocket hook can't directly touch the
        // RoomsProvider, so we re-emit on the window and let the
        // provider listen. Detail mirrors the server frame so future
        // consumers (toasts, focus-the-new-room flows) can read it
        // without parsing again.
        window.dispatchEvent(new CustomEvent('doorae:rooms:invalidate', { detail: data }));
      } else if (data.type === 'room_pin_order_changed') {
        // Sidebar pin / reorder landed in another session of the
        // same user (#47). We forward it to the RoomsProvider via
        // a dedicated window event because the payload carries the
        // exact new order — the provider can apply it directly
        // without a refetch round-trip. Shape: { user_id, pinned_room_ids }.
        window.dispatchEvent(
          new CustomEvent('doorae:rooms:pin-order', { detail: data }),
        )
      } else if (data.type === 'room_deleted') {
        // The whole room is gone. Bubble two events:
        //   1. ``doorae:rooms:invalidate`` — same listener
        //      ``RoomsProvider`` already uses for membership changes,
        //      so the sidebar drops the room without us having to
        //      reach into the store.
        //   2. ``doorae:room:deleted`` — carries the room_id so a
        //      page currently *viewing* that room can navigate
        //      away (otherwise the user is left staring at a 404
        //      or an empty chat view).
        window.dispatchEvent(new CustomEvent('doorae:rooms:invalidate', { detail: data }));
        window.dispatchEvent(new CustomEvent('doorae:room:deleted', { detail: data }));
      } else if (data.type === 'room_settings_changed') {
        // #237 — forward settings change so ``useRooms`` updates its
        // cached ephemeral flag on other tabs / other open sessions.
        window.dispatchEvent(
          new CustomEvent('doorae:rooms:settings-changed', { detail: data }),
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
          new CustomEvent('doorae:presence:update', { detail: data }),
        );
      } else if (data.type === 'task.updated') {
        // #266 — task lifecycle event. The 1차 view (TaskPanel) and
        // the 2차 view (AgentTasksTab) both subscribe via window
        // events because they live outside the per-room hook tree
        // (TaskPanel's filter state, AgentTasksTab's agent_id scope).
        // Detail shape: { type, event, task: {...} }.
        window.dispatchEvent(
          new CustomEvent('doorae:task:updated', { detail: data }),
        );
      } else if (data.type === 'room_artifact.added') {
        // #290 — agent dropped a new file in memory/outbox/. The
        // RoomArtifactsDialog (and any future right-rail panel)
        // listens on the window so it doesn't need to be wired into
        // the WS hook's prop tree. Detail mirrors the server frame.
        window.dispatchEvent(
          new CustomEvent('doorae:room_artifact:added', { detail: data }),
        );
      } else if (data.type === 'room_artifact.removed') {
        window.dispatchEvent(
          new CustomEvent('doorae:room_artifact:removed', { detail: data }),
        );
      } else if (data.type === 'typing') {
        const pid = data.participant_id;
        if (data.is_typing) {
          setTypingUsers(prev => new Set(prev).add(pid));
          // Reset the expire timer — don't stack multiple timeouts
          if (typingTimers.current[pid]) clearTimeout(typingTimers.current[pid]);
          typingTimers.current[pid] = setTimeout(() => {
            setTypingUsers(prev => {
              const next = new Set(prev); next.delete(pid); return next;
            });
            delete typingTimers.current[pid];
          }, 5000);
        } else {
          if (typingTimers.current[pid]) {
            clearTimeout(typingTimers.current[pid]);
            delete typingTimers.current[pid];
          }
          setTypingUsers(prev => {
            const next = new Set(prev); next.delete(pid); return next;
          });
        }
      }
    };
  }, [roomId]);

  useEffect(() => {
    setMessages([]);
    seqRef.current = 0;
    suppressReconnectRef.current = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
    connect();
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); wsRef.current = null; }
    };
  }, [roomId, connect]);

  const send = useCallback((content: string, metadata?: Record<string, unknown>) => {
    const frame: Record<string, unknown> = { type: 'send', content }
    if (metadata && Object.keys(metadata).length > 0) frame.metadata = metadata
    wsRef.current?.send(JSON.stringify(frame));
  }, []);

  const sendTyping = useCallback((isTyping: boolean) => {
    wsRef.current?.send(JSON.stringify({ type: 'typing', is_typing: isTyping }));
  }, []);

  // Load history via REST on mount
  useEffect(() => {
    if (!roomId) return;
    const token = getAuthToken();
    if (!token) return;
    fetch(`/api/v1/rooms/${roomId}/messages?since_seq=0&limit=100`, {
      headers: { 'Authorization': `Bearer ${token}` },
    }).then(r => {
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
            new CustomEvent('doorae:auth:invalid', {
              detail: { status: r.status },
            }),
          );
        }
        return [];
      }
      return r.ok ? r.json() : [];
    }).then(msgs => {
      if (msgs.length) {
        setMessages(msgs);
        seqRef.current = Math.max(...msgs.map((m: ChatMessage) => m.seq));
      }
    }).catch(() => {});
  }, [roomId]);

  return { messages, connected, typingUsers, send, sendTyping };
}
