import { useState, useRef, useCallback, useEffect } from 'react';

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
  // Debounced typing expire timers — one per participant.
  // Each new typing=true RESETS the timer instead of stacking.
  const typingTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const connect = useCallback(() => {
    if (!roomId) return;
    const token = localStorage.getItem('doorae_token');
    if (!token) return;

    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    let url = `${proto}//${host}/ws/rooms/${roomId}`;
    if (seqRef.current > 0) url += `?since_seq=${seqRef.current}`;

    const ws = new WebSocket(url, ['doorae.v1', `bearer.${token}`]);
    wsRef.current = ws;

    ws.onopen = () => { setConnected(true); reconnectRef.current = 1; };
    ws.onclose = () => {
      setConnected(false);
      const delay = Math.min(reconnectRef.current, 30);
      reconnectRef.current = Math.min(delay * 2, 30);
      setTimeout(connect, delay * 1000);
    };
    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === 'message') {
        if (data.seq > seqRef.current) seqRef.current = data.seq;
        setMessages(prev => {
          if (prev.some(m => m.seq === data.seq)) return prev;
          return [...prev, data];
        });
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
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
    connect();
    return () => { if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); } };
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
    const token = localStorage.getItem('doorae_token');
    if (!token) return;
    fetch(`/api/v1/rooms/${roomId}/messages?since_seq=0&limit=100`, {
      headers: { 'Authorization': `Bearer ${token}` },
    }).then(r => r.ok ? r.json() : []).then(msgs => {
      if (msgs.length) {
        setMessages(msgs);
        seqRef.current = Math.max(...msgs.map((m: ChatMessage) => m.seq));
      }
    }).catch(() => {});
  }, [roomId]);

  return { messages, connected, typingUsers, send, sendTyping };
}
