// ChatClient — WebSocket client with per-room reconnection,
// since_seq recovery, and message-handler callbacks.
//
// Ported from ``packages/agent/anygarden_agent/client.py``.
//
// The class exposes the same shape the Python version does so adapter
// code reads identically across languages:
//
// - ``joinRoom(roomId)`` starts the per-room loop (fire-and-forget).
// - ``send(roomId, content, metadata?)`` attaches a nonce and emits a
//   SendFrame.
// - ``onMessage(handler)`` registers a message callback. Multiple
//   handlers are dispatched in order; an exception in one handler is
//   logged and does not block the others.
// - ``run()`` resolves only when all room loops exit (or ``close()``
//   is called). Mirrors ``ChatClient.run`` in Python.
// - ``close()`` cancels all loops and closes every WS.
//
// The heavy lifting is in ``_processFrame`` which encapsulates
// hard/soft self-filter + turn counter + dispatch. It's deliberately
// kept pure-ish (takes a frame dict, returns nothing, relies on
// internal state) so we can exercise it without a real WS server.

import { WebSocket, type RawData } from "ws";
import { TurnCounter } from "./routing/turn-counter.js";
import { NonceTracker } from "./routing/nonce.js";
import { buildSubprotocols } from "./protocol/auth.js";
import {
  safeParseOutgoingFrame,
  type MessageOut,
  type OutgoingFrame,
  type WelcomeOut,
} from "./protocol/frames.js";
import { log } from "./logging.js";

export type MessageHandler = (msg: MessageOut) => Promise<void> | void;
export type JoinHandler = (roomId: string) => Promise<void> | void;

export interface ChatClientOptions {
  serverUrl: string;
  token: string;
  agentName?: string;
  maxReconnectDelay?: number;
  /**
   * Initial backoff in ms. Production default matches the Python
   * client (1 000 ms). Tests lower this to keep the reconnect loop
   * fast without resorting to fake timers.
   */
  initialReconnectDelay?: number;
  /** Only set by tests — lets us inject a fake WebSocket ctor. */
  webSocketCtor?: WebSocketCtor;
  /** Only set by tests — overrides ``setTimeout``/``clearTimeout``. */
  now?: () => number;
  /** Only set by tests — stub fetch/http; default uses Node 20 global. */
  httpFetch?: typeof fetch;
}

// Minimal surface we need from the ``ws`` WebSocket. Test doubles
// implement this without pulling in the real library.
export interface WSLike {
  send(data: string): void;
  close(): void;
  on(event: "message", cb: (data: RawData) => void): void;
  on(event: "open", cb: () => void): void;
  on(event: "close", cb: (code: number, reason: Buffer) => void): void;
  on(event: "error", cb: (err: Error) => void): void;
  on(event: "unexpected-response", cb: (req: unknown, res: { statusCode?: number }) => void): void;
}

export type WebSocketCtor = new (url: string, protocols: string[]) => WSLike;

// HTTP URL derived from the WS URL — the REST endpoints share the host.
function toHttpBase(wsUrl: string): string {
  return wsUrl.replace(/^ws(s?):\/\//, "http$1://");
}

export class ChatClient {
  readonly serverUrl: string;
  readonly agentName: string;
  private readonly token: string;
  private readonly maxReconnectDelay: number;
  private readonly initialReconnectDelay: number;
  private readonly wsCtor: WebSocketCtor;
  private readonly httpFetch: typeof fetch;

  private readonly lastSeq = new Map<string, number>();
  private readonly connections = new Map<string, WSLike>();
  private readonly loops = new Map<string, Promise<void>>();
  private readonly cancels = new Map<string, () => void>();

  private readonly messageHandlers: MessageHandler[] = [];
  private readonly joinHandlers: JoinHandler[] = [];

  private readonly nonceTracker = new NonceTracker();
  private readonly turnCounter = new TurnCounter();

  /** Participant IDs the server has assigned us (one per room). */
  readonly myParticipantIds = new Set<string>();
  /** Agent identity from the welcome frame, if the conn is agent-auth. */
  agentId: string | null = null;

  private running = false;

  constructor(opts: ChatClientOptions) {
    this.serverUrl = opts.serverUrl.replace(/\/+$/, "");
    this.token = opts.token;
    this.agentName = opts.agentName ?? "";
    this.maxReconnectDelay = opts.maxReconnectDelay ?? 60_000;
    this.initialReconnectDelay = opts.initialReconnectDelay ?? 1_000;
    this.wsCtor = opts.webSocketCtor ?? (WebSocket as unknown as WebSocketCtor);
    this.httpFetch = opts.httpFetch ?? fetch;
  }

  // ── Callback registration ──────────────────────────────────────────

  onMessage(handler: MessageHandler): MessageHandler {
    this.messageHandlers.push(handler);
    return handler;
  }

  offMessage(handler: MessageHandler): void {
    const i = this.messageHandlers.indexOf(handler);
    if (i >= 0) this.messageHandlers.splice(i, 1);
  }

  onJoinRoom(handler: JoinHandler): JoinHandler {
    this.joinHandlers.push(handler);
    return handler;
  }

  // ── Public API ────────────────────────────────────────────────────

  async joinRoom(roomId: string): Promise<void> {
    if (this.loops.has(roomId)) {
      log.warn({ room_id: roomId }, "room.already_joined");
      return;
    }
    if (!this.lastSeq.has(roomId)) this.lastSeq.set(roomId, 0);
    this.running = true;
    const cancel = new AbortController();
    this.cancels.set(roomId, () => cancel.abort());
    const loop = this.roomLoop(roomId, cancel.signal);
    this.loops.set(roomId, loop);
  }

  /**
   * Send a message to ``roomId``. A fresh nonce is attached so the
   * echo-back is filtered on the receive side. Throws if we're not
   * connected to the room.
   */
  async send(
    roomId: string,
    content: string,
    metadata?: Record<string, unknown> | null,
  ): Promise<void> {
    const ws = this.connections.get(roomId);
    if (!ws) throw new Error(`Not connected to room ${roomId}`);
    const md: Record<string, unknown> = metadata ? { ...metadata } : {};
    md._nonce = this.nonceTracker.allocate();
    const frame = { type: "send", content, metadata: md };
    ws.send(JSON.stringify(frame));
  }

  /**
   * Send a typing indicator. Silently swallows errors: a best-effort
   * signal that shouldn't tear down the caller's work when the WS is
   * momentarily unavailable.
   */
  async sendTyping(roomId: string, isTyping: boolean): Promise<void> {
    const ws = this.connections.get(roomId);
    if (!ws) return;
    try {
      ws.send(JSON.stringify({ type: "typing", is_typing: isTyping }));
    } catch {
      // best-effort
    }
  }

  /**
   * Fetch participant list for a room via REST. Mirrors
   * ``ChatClient.get_room_participants`` in Python.
   */
  async getRoomParticipants(roomId: string): Promise<Record<string, unknown>[]> {
    const base = toHttpBase(this.serverUrl);
    const resp = await this.httpFetch(`${base}/api/v1/rooms/${roomId}`, {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!resp.ok) return [];
    const data = (await resp.json()) as { participants?: Record<string, unknown>[] };
    return data.participants ?? [];
  }

  /** Find a sub-room by name under a parent. Returns the id or null. */
  async findSubRoom(parentRoomId: string, name: string): Promise<string | null> {
    const base = toHttpBase(this.serverUrl);
    const url = new URL(`${base}/api/v1/rooms/${parentRoomId}/sub-rooms`);
    url.searchParams.set("name", name);
    const resp = await this.httpFetch(url.toString(), {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!resp.ok) return null;
    const rooms = (await resp.json()) as Array<{ id: string }>;
    return rooms[0]?.id ?? null;
  }

  isConnected(roomId: string): boolean {
    return this.connections.has(roomId);
  }

  joinedRooms(): string[] {
    return [...this.loops.keys()];
  }

  /** Wait for all room loops to finish. */
  async run(): Promise<void> {
    this.running = true;
    if (this.loops.size > 0) {
      await Promise.allSettled(this.loops.values());
    }
    this.running = false;
  }

  /** Cancel all loops and close connections. */
  async close(): Promise<void> {
    this.running = false;
    for (const cancel of this.cancels.values()) cancel();
    this.cancels.clear();
    for (const ws of this.connections.values()) {
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
    this.connections.clear();
    if (this.loops.size > 0) {
      await Promise.allSettled(this.loops.values());
    }
    this.loops.clear();
  }

  // ── Test-visible helpers ───────────────────────────────────────────

  /**
   * Directly feed a parsed outgoing frame to the dispatch pipeline.
   * Used by unit tests to drive ``_processFrame`` without running a
   * real WS server. Not part of the public API — prefer real joins
   * for integration tests.
   */
  async __testFeedFrame(roomId: string, frame: OutgoingFrame): Promise<void> {
    await this.processFrame(roomId, frame);
  }

  /**
   * Drain a set of outgoing frames from the connection side in tests.
   * Used by coordination tests that want to capture what the agent
   * wrote to the wire; see ``tests/client.test.ts``.
   */
  __testSetConnection(roomId: string, ws: WSLike): void {
    this.connections.set(roomId, ws);
    if (!this.lastSeq.has(roomId)) this.lastSeq.set(roomId, 0);
    this.loops.set(roomId, Promise.resolve());
  }

  // ── Internal — message dispatch ────────────────────────────────────

  /**
   * Handle a single parsed OutgoingFrame.
   *
   * Implements the same decision tree as the Python ``_process_frame``:
   * 1. ``welcome`` → cache ``participant_id`` + ``agent_id``, join
   *    any ``pending_rooms`` we don't already have.
   * 2. ``join_room`` → join the referenced room if we're not already
   *    in it.
   * 3. ``message`` →
   *    a. Hard self-filter by ``participant_id`` (counts toward turn
   *       counter via ``TurnCounter.handleSelf``).
   *    b. Soft self-filter by ``_nonce``.
   *    c. Turn-counter gate (``handleIncoming``). ``skip_limit`` logs
   *       ``ws.agent_turn_limit`` and drops.
   *    d. Dispatch to every registered handler.
   * 4. ``error`` → log at warn. Other frame types are observed but
   *    not dispatched.
   */
  private async processFrame(roomId: string, frame: OutgoingFrame): Promise<void> {
    switch (frame.type) {
      case "welcome":
        this.handleWelcome(roomId, frame);
        return;
      case "join_room": {
        if (!this.loops.has(frame.room_id)) {
          log.info({ room_id: frame.room_id, via: roomId }, "ws.dynamic_join");
          await this.joinRoom(frame.room_id);
        }
        return;
      }
      case "message":
        await this.handleMessage(roomId, frame);
        return;
      case "error":
        log.warn({ detail: frame.detail }, "ws.server_error");
        return;
      default:
        // typing/presence/room_created/etc. pass through silently —
        // the Python client does the same. Handlers that want them
        // can layer on top via ``onMessage`` only after we start
        // dispatching them; for now they're informational.
        return;
    }
  }

  private handleWelcome(roomId: string, frame: WelcomeOut): void {
    if (frame.participant_id) {
      this.myParticipantIds.add(frame.participant_id);
      log.info(
        { room_id: roomId, participant_id: frame.participant_id },
        "ws.welcome",
      );
    }
    // Issue #61 — only overwrite if server sent a value.
    if (frame.agent_id) this.agentId = frame.agent_id;
    for (const pending of frame.pending_rooms ?? []) {
      if (!this.loops.has(pending)) {
        log.info({ room_id: pending, via: roomId }, "ws.pending_room_join");
        // Fire-and-forget — the room loop starts asynchronously and
        // mirrors the Python semantics (``await join_room`` inside
        // the welcome handler).
        void this.joinRoom(pending);
      }
    }
  }

  private async handleMessage(roomId: string, frame: MessageOut): Promise<void> {
    if (frame.seq > (this.lastSeq.get(roomId) ?? 0)) {
      this.lastSeq.set(roomId, frame.seq);
    }

    const sender = frame.participant_id;
    const content = frame.content ?? "";

    // a. Hard self-filter.
    if (sender && this.myParticipantIds.has(sender)) {
      this.turnCounter.handleSelf(roomId, content);
      return;
    }

    // b. Soft self-filter via nonce.
    const metadata = frame.metadata ?? {};
    const nonce = typeof metadata._nonce === "string" ? metadata._nonce : null;
    if (nonce && this.nonceTracker.consume(nonce)) {
      this.turnCounter.handleNonceEcho(roomId, content);
      return;
    }

    // c. Turn-counter gate. ``senderHasNonce`` is the heuristic for
    //    "sender is an agent" (agent messages carry ``_nonce``,
    //    human messages don't — they're sent by the user UI which
    //    doesn't emit the field).
    const senderHasNonce = typeof metadata._nonce === "string";
    const decision = this.turnCounter.handleIncoming(roomId, content, senderHasNonce);
    if (decision.outcome === "skip_limit") {
      log.info(
        { room_id: roomId, count: decision.count, limit: this.turnCounter.max },
        "ws.agent_turn_limit",
      );
      return;
    }

    // d. Dispatch.
    for (const handler of this.messageHandlers) {
      try {
        await handler(frame);
      } catch (exc) {
        log.error({ error: String(exc) }, "handler.message_error");
      }
    }
  }

  // ── Internal — WS loop ─────────────────────────────────────────────

  private async roomLoop(roomId: string, signal: AbortSignal): Promise<void> {
    let delay = this.initialReconnectDelay;
    while (!signal.aborted) {
      let url = `${this.serverUrl}/ws/rooms/${roomId}`;
      const since = this.lastSeq.get(roomId) ?? 0;
      if (since > 0) url += `?since_seq=${since}`;

      const subprotocols = buildSubprotocols(this.token);
      let ws: WSLike;
      try {
        ws = new this.wsCtor(url, subprotocols);
      } catch (err) {
        log.warn({ room_id: roomId, error: String(err), retry_in: delay }, "ws.disconnected");
        await this.sleepWithAbort(delay, signal);
        delay = Math.min(delay * 2, this.maxReconnectDelay);
        continue;
      }

      const opened = await this.attachWs(roomId, ws, signal);
      if (!opened.ok) {
        if (opened.giveUp) {
          log.warn({ room_id: roomId }, "ws.not_member_giving_up");
          this.loops.delete(roomId);
          return;
        }
        log.warn(
          { room_id: roomId, error: opened.reason ?? "closed", retry_in: delay },
          "ws.disconnected",
        );
        await this.sleepWithAbort(delay, signal);
        delay = Math.min(delay * 2, this.maxReconnectDelay);
        continue;
      }

      // Reset backoff on successful connect. ``attachWs`` resolves
      // only when the socket closes, so by this point the connection
      // was up for at least one read. Loop around to reconnect.
      delay = this.initialReconnectDelay;
    }
  }

  private sleepWithAbort(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      if (signal.aborted) {
        resolve();
        return;
      }
      const t = setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      const onAbort = () => {
        clearTimeout(t);
        resolve();
      };
      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  /**
   * Wire up one WS and resolve when it closes. Returns ``ok: true``
   * if we got past the open handshake; ``giveUp: true`` for 403/4003
   * (not a member) so the room loop can stop retrying.
   */
  private attachWs(
    roomId: string,
    ws: WSLike,
    signal: AbortSignal,
  ): Promise<{ ok: boolean; giveUp?: boolean; reason?: string }> {
    return new Promise((resolve) => {
      let settled = false;
      const settle = (r: { ok: boolean; giveUp?: boolean; reason?: string }) => {
        if (settled) return;
        settled = true;
        this.connections.delete(roomId);
        resolve(r);
      };

      ws.on("open", () => {
        this.connections.set(roomId, ws);
        log.info({ room_id: roomId, agent: this.agentName }, "ws.connected");
        for (const handler of this.joinHandlers) {
          // Fire-and-forget — one misbehaving handler must not hold
          // the open path.
          Promise.resolve(handler(roomId)).catch((exc) =>
            log.error({ error: String(exc) }, "handler.join_error"),
          );
        }
      });

      ws.on("message", (raw) => {
        let text: string;
        if (typeof raw === "string") {
          text = raw;
        } else if (Array.isArray(raw)) {
          text = Buffer.concat(raw).toString("utf-8");
        } else if (raw instanceof Buffer || raw instanceof ArrayBuffer) {
          text = Buffer.from(raw as Buffer).toString("utf-8");
        } else {
          text = String(raw);
        }
        let data: unknown;
        try {
          data = JSON.parse(text);
        } catch {
          log.warn({ length: text.length }, "ws.bad_frame");
          return;
        }
        const frame = safeParseOutgoingFrame(data);
        if (!frame) {
          log.warn({ type: (data as { type?: unknown })?.type }, "ws.bad_frame");
          return;
        }
        // The WS library doesn't expose a promise here — we process
        // frames sequentially via an in-order chain so async handlers
        // don't interleave mid-frame.
        this.enqueueFrame(roomId, frame);
      });

      ws.on("unexpected-response", (_req, res) => {
        const code = res.statusCode ?? 0;
        if (code === 403) {
          settle({ ok: true, giveUp: true });
        } else {
          settle({ ok: false, reason: `http_${code}` });
        }
      });

      ws.on("close", (code) => {
        // WS close code 4003 mirrors the Python "not a member" path.
        if (code === 4003) {
          settle({ ok: true, giveUp: true });
          return;
        }
        settle({ ok: true });
      });

      ws.on("error", (err) => {
        settle({ ok: false, reason: String(err) });
      });

      signal.addEventListener(
        "abort",
        () => {
          try {
            ws.close();
          } catch {
            // ignore
          }
          settle({ ok: true });
        },
        { once: true },
      );
    });
  }

  // Per-room serial frame dispatch — keeps async handlers in order.
  private readonly frameChains = new Map<string, Promise<void>>();
  private enqueueFrame(roomId: string, frame: OutgoingFrame): void {
    const prev = this.frameChains.get(roomId) ?? Promise.resolve();
    const next = prev
      .then(() => this.processFrame(roomId, frame))
      .catch((exc) => log.error({ error: String(exc) }, "frame.process_error"));
    this.frameChains.set(roomId, next);
  }
}
