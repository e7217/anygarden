"""ChatClient — WebSocket client with reconnection and callback support."""

from __future__ import annotations

import asyncio
import collections
import json
import uuid
from typing import Any, Callable, Coroutine

import httpx
import structlog
import websockets
from websockets.asyncio.client import connect as ws_connect

from doorae_agent.integrations.cycle_guard import hash_content
from doorae_agent.protocol.frames import MessageOut, SendFrame
from doorae_agent.protocol.versioning import build_subprotocols

logger = structlog.get_logger(__name__)

# Type alias for message handlers
MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


def _is_task_init_content(content: str) -> bool:
    """Return True when ``content`` starts a new task and should reset
    the per-room agent-only turn counter.

    Issue #67 — agent-only rooms accumulate the counter across task
    boundaries because the hard/soft filter paths (for self-sent and
    nonce-echo messages) only know how to ``+1`` and early return.
    A representative agent that emits consecutive ``[ROOM_QUERY]``
    rounds therefore drives the counter past ``max_agent_turns`` and
    later replies are dropped.

    The three recognised task-init prefixes are:

    - ``[ROOM_QUERY]`` — the representative agent forwards a room
      query to another room. Each forward is an independent task.
    - ``[DELEGATED]``  — a user/agent delegates a subtask to another
      agent. Each delegation is an independent task.
    - ``[HANDOFF]`` (#159 Phase C) — the orchestrator passes turn
      control to another participant via the ``handoff_to`` tool.
      The receiving agent treats this as a fresh task so the
      per-room agent-turn counter doesn't age out mid-collaboration.
    """
    return (
        content.startswith("[ROOM_QUERY]")
        or content.startswith("[DELEGATED]")
        or content.startswith("[HANDOFF]")
    )


class ChatClient:
    """Async WebSocket client for Doorae chat rooms.

    Supports:
    - Multi-room connections
    - Automatic reconnection with exponential backoff
    - since_seq recovery on reconnect
    - Decorator-based message callbacks
    """

    def __init__(
        self,
        server_url: str,
        token: str,
        agent_name: str = "",
        *,
        max_reconnect_delay: float = 60.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._agent_name = agent_name
        self._max_reconnect_delay = max_reconnect_delay

        # room_id -> last seen sequence number
        self._last_seq: dict[str, int] = {}
        # room_id -> websocket connection
        self._connections: dict[str, Any] = {}
        # room_id -> asyncio task
        self._tasks: dict[str, asyncio.Task] = {}

        # Callbacks
        self._message_handlers: list[MessageHandler] = []
        self._join_handlers: list[Callable[..., Any]] = []

        # Self-echo filtering: nonces of messages we sent
        self._sent_nonces: set[str] = set()
        # Hard self-message filter: participant IDs assigned to us by
        # the server (one per room).  Messages from any of these IDs
        # are always skipped, even if the nonce was lost (e.g. after a
        # reconnect or when a duplicate process shares our token).
        self._my_participant_ids: set[str] = set()

        # Issue #61 — the agent identity this client is bound to.
        # Populated from the welcome frame (server sends ``agent_id``
        # only for agent-authenticated connections). ``None`` when the
        # connection is authenticated as a user/guest, or when the
        # server is running a pre-#61 build that doesn't send the
        # field. ``should_respond`` uses this to gate ``room_query``
        # forwarding: only the representative agent should forward the
        # [ROOM_QUERY], otherwise N agents in the source room send N
        # duplicates to the target room.
        self._agent_id: str | None = None

        # Issue #148 Part 3 — cached agent-side opt-out from ambient
        # context ingestion. Refreshed from every welcome frame the
        # server sends, so toggling the flag via the admin UI plus a
        # ``bump_generation`` (which respawns the agent subprocess)
        # propagates on the next ws connect. ``decide_policy`` reads
        # this to demote ``ingest_only`` broadcasts to ``SKIP`` for
        # opt-out agents.
        self._context_window_opt_out: bool = False

        # Per-room agent-only consecutive message counter.
        # Counts how many messages in a row came from agents (non-human)
        # without a human message in between.  When the count exceeds
        # max_agent_turns, the handler skips the message to prevent
        # infinite agent-to-agent loops.  A human message resets to 0.
        self._agent_turn_count: dict[str, int] = {}
        self.max_agent_turns: int = 6

        # Issue #157 Phase A — reset-prefix abuse guard.
        # ``[ROOM_QUERY]`` / ``[DELEGATED]`` frames reset the agent-
        # turn counter (issue #67), which a looping agent can exploit
        # to evade ``max_agent_turns`` by emitting task-init prefixes
        # in a loop. This counter tracks consecutive task-init resets
        # per room; once it exceeds ``max_task_init_resets`` the reset
        # no longer fires and ``max_agent_turns`` regains its bite.
        # A non-self, non-nonce (human) message resets the streak.
        self._consecutive_task_init: dict[str, int] = {}
        self.max_task_init_resets: int = 5

        # Issue #159 Phase A — room-scoped speaker strategy caches.
        # The server sets these on every welcome frame so the SDK can
        # dispatch in ``decide_policy``. Defaults preserve the legacy
        # behaviour for rooms that predate the schema.
        self._speaker_strategy: dict[str, str] = {}
        self._orchestrator_agent_id: dict[str, str | None] = {}
        self._next_speaker_participant_id: dict[str, str | None] = {}

        # Issue #157 Phase B — per-room ring buffer of recent message
        # fingerprints (sender, hash). Feeds ``cycle_guard`` in
        # ``decide_policy``: when the same (sender, hash) pair has
        # appeared ``min_repetitions`` times within ``window`` entries,
        # the agent skips the message to break semantic loops that
        # ``max_agent_turns`` / reset-guard can't catch. Short messages
        # hash to None and never enter the buffer, protecting "ok"-style
        # legitimate repeats.
        self._recent_msgs: dict[
            str, collections.deque[dict[str, str]]
        ] = {}
        self._recent_msgs_maxlen: int = 10

        # HTTP client for REST calls
        self._http: httpx.AsyncClient | None = None
        self._running = False

    def __repr__(self) -> str:
        return (
            f"ChatClient(server={self._server_url!r}, "
            f"agent={self._agent_name!r}, "
            f"rooms={list(self._tasks.keys())!r})"
        )

    # ── Callback decorators ──────────────────────────────────────────

    def on_message(self, handler: MessageHandler) -> MessageHandler:
        """Register a message callback (decorator)."""
        self._message_handlers.append(handler)
        return handler

    def on_join_room(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Register a join room callback (decorator)."""
        self._join_handlers.append(handler)
        return handler

    # ── Room operations ──────────────────────────────────────────────

    async def join_room(self, room_id: str) -> None:
        """Start listening on a room via WebSocket."""
        if room_id in self._tasks:
            logger.warning("room.already_joined", room_id=room_id)
            return
        self._last_seq.setdefault(room_id, 0)
        task = asyncio.create_task(self._room_loop(room_id))
        self._tasks[room_id] = task

    async def send(
        self,
        room_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Send a message to a room."""
        ws = self._connections.get(room_id)
        if ws is None:
            raise RuntimeError(f"Not connected to room {room_id}")
        # Attach a nonce so we can filter our own echo on receive
        nonce = str(uuid.uuid4())
        metadata = dict(metadata) if metadata else {}
        metadata["_nonce"] = nonce
        self._sent_nonces.add(nonce)
        frame = SendFrame(content=content, metadata=metadata)
        await ws.send(frame.model_dump_json())

    async def sendTyping(self, room_id: str, is_typing: bool) -> None:
        """Send a typing indicator to a room."""
        ws = self._connections.get(room_id)
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"type": "typing", "is_typing": is_typing}))
        except Exception:
            pass

    async def find_sub_room(self, parent_room_id: str, name: str) -> str | None:
        """Find a sub-room by name. Returns room_id or None."""
        http = await self._get_http()
        base = self._server_url.replace("ws://", "http://").replace("wss://", "https://")
        resp = await http.get(
            f"{base}/api/v1/rooms/{parent_room_id}/sub-rooms",
            params={"name": name},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code != 200:
            return None
        rooms = resp.json()
        return rooms[0]["id"] if rooms else None

    async def get_room_participants(self, room_id: str) -> list[dict]:
        """Fetch participant list for a room via REST API."""
        http = await self._get_http()
        base = self._server_url.replace("ws://", "http://").replace("wss://", "https://")
        resp = await http.get(
            f"{base}/api/v1/rooms/{room_id}",
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("participants", [])

    async def create_sub_room(
        self,
        parent_room_id: str,
        participants: list[str],
        purpose: str,
    ) -> str:
        """Create a sub-channel via the REST API. Returns the new room ID."""
        http = await self._get_http()
        # Derive the REST base URL from the WS URL
        base = self._server_url.replace("ws://", "http://").replace("wss://", "https://")
        resp = await http.post(
            f"{base}/api/v1/rooms/{parent_room_id}/sub-rooms",
            json={
                "name": purpose,
                "participants": participants,
                "is_dm": False,
                "creator_participant_id": "",  # server will validate
            },
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        room_id = data["id"]
        await self.join_room(room_id)
        return room_id

    # ── Lifecycle ────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main event loop — waits for all room tasks to complete."""
        self._running = True
        logger.info("client.running", agent=self._agent_name, rooms=list(self._tasks.keys()))
        try:
            if self._tasks:
                await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def close(self) -> None:
        """Close all connections and cancel tasks."""
        self._running = False
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        # Wait for tasks to finish cancellation
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for ws in self._connections.values():
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._tasks.clear()
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Internal ─────────────────────────────────────────────────────

    def _record_recent_message(
        self, room_id: str, msg: dict[str, Any]
    ) -> None:
        """Append a (sender, hash) fingerprint to the room's ring
        buffer for ``cycle_guard`` (#157 Phase B).

        Short content hashes to ``None`` and is skipped — legitimate
        short repeats ("ok", "네", "done") must not feed the detector.
        Missing sender is also skipped (welcome/system frames).
        """
        sender = msg.get("participant_id")
        if not sender:
            return
        content = msg.get("content", "") or ""
        h = hash_content(content)
        if h is None:
            return
        buf = self._recent_msgs.get(room_id)
        if buf is None:
            buf = collections.deque(maxlen=self._recent_msgs_maxlen)
            self._recent_msgs[room_id] = buf
        buf.append({"sender": sender, "hash": h})

    def _consume_task_init_reset(self, room_id: str) -> bool:
        """Increment the per-room consecutive task-init counter and
        return whether the caller should still honour the reset.

        Issue #157 Phase A — returns False once more than
        ``max_task_init_resets`` consecutive task-init frames have
        arrived without a human message breaking the streak. The
        caller must then keep ``_agent_turn_count`` as-is so
        ``max_agent_turns`` can still fire on runaway loops that
        spam ``[ROOM_QUERY]`` / ``[DELEGATED]`` prefixes.
        """
        count = self._consecutive_task_init.get(room_id, 0) + 1
        self._consecutive_task_init[room_id] = count
        if count > self.max_task_init_resets:
            logger.warning(
                "task_init.reset_guard_fired",
                room_id=room_id,
                consecutive=count,
                limit=self.max_task_init_resets,
            )
            return False
        return True

    async def _process_frame(self, room_id: str, data: dict[str, Any]) -> None:
        """Handle a single incoming WS frame (called from _room_loop)."""
        msg_type = data.get("type")
        if msg_type == "message":
            # Issue #157 Phase B — record the message for cycle detection
            # before any early-return filters fire. Self / nonce-echo
            # frames still count: the detector tracks (sender, hash)
            # pairs, so repeats from this very agent are also caught.
            self._record_recent_message(room_id, data)

            seq = data.get("seq", 0)
            if seq > self._last_seq.get(room_id, 0):
                self._last_seq[room_id] = seq

            # Hard filter: skip messages sent by our own participant.
            sender = data.get("participant_id")
            if sender and sender in self._my_participant_ids:
                # Issue #67 — a self-emitted ``[ROOM_QUERY]``/
                # ``[DELEGATED]`` is a task boundary even though the
                # frame is our own echo. Reset so agent-only rooms
                # don't inherit the previous exchange's count on the
                # next task round.  Regular self-messages still count
                # toward the limit to bound total agent-only traffic.
                # Issue #157 Phase A — ``_consume_task_init_reset``
                # drops the reset once consecutive task-inits exceed
                # ``max_task_init_resets``, re-arming ``max_agent_turns``.
                content = data.get("content", "")
                if _is_task_init_content(content):
                    if self._consume_task_init_reset(room_id):
                        self._agent_turn_count[room_id] = 0
                else:
                    self._agent_turn_count[room_id] = (
                        self._agent_turn_count.get(room_id, 0) + 1
                    )
                return

            # Soft filter: skip our own echoes via nonce
            msg_meta = data.get("metadata") or {}
            nonce = msg_meta.get("_nonce")
            if nonce and nonce in self._sent_nonces:
                self._sent_nonces.discard(nonce)
                # Issue #67 — same task-boundary semantics apply when
                # the echo arrives via the nonce path (e.g. the hard
                # filter missed it because participant_id changed).
                # Issue #157 Phase A — guard mirrors the hard-filter path.
                content = data.get("content", "")
                if _is_task_init_content(content):
                    if self._consume_task_init_reset(room_id):
                        self._agent_turn_count[room_id] = 0
                else:
                    self._agent_turn_count[room_id] = (
                        self._agent_turn_count.get(room_id, 0) + 1
                    )
                return

            # Turn counter: track consecutive agent-only messages.
            # A message from a non-self participant is either from
            # a human (reset counter) or another agent (increment).
            # We use nonce presence as a heuristic: agent messages
            # have _nonce (set by SDK), human messages don't.
            content = data.get("content", "")
            sender_has_nonce = bool(msg_meta.get("_nonce"))

            # [DELEGATED] and [ROOM_QUERY] messages are new task
            # initiations — always reset the counter so the handler
            # processes the task even if the previous agent-only
            # exchange hit the limit.
            # Issue #157 Phase A — once consecutive task-inits exceed
            # ``max_task_init_resets`` the reset no longer fires, so
            # ``max_agent_turns`` regains authority over prefix-looping
            # agents.
            if _is_task_init_content(content):
                if self._consume_task_init_reset(room_id):
                    self._agent_turn_count[room_id] = 0
            elif sender_has_nonce:
                # From another agent — increment
                count = self._agent_turn_count.get(room_id, 0) + 1
                self._agent_turn_count[room_id] = count
                if count > self.max_agent_turns:
                    logger.info(
                        "ws.agent_turn_limit",
                        room_id=room_id,
                        count=count,
                        limit=self.max_agent_turns,
                    )
                    return  # skip — agent-only loop exceeded limit
            else:
                # From a human — reset both counters. Human messages
                # break the agent-only streak *and* clear the task-init
                # consecutive counter that feeds the #157 guard.
                self._agent_turn_count[room_id] = 0
                self._consecutive_task_init[room_id] = 0

            # Issue #157 Phase B — surface the room_id on the frame so
            # ``decide_policy`` can look up the per-room recent-message
            # ring buffer. Adapters already read ``msg.get("room_id")``
            # (see claude_code.py), this makes the field authoritative.
            data.setdefault("room_id", room_id)

            for handler in self._message_handlers:
                try:
                    await handler(data)
                except Exception as exc:
                    logger.error("handler.message_error", error=str(exc))
        elif msg_type == "welcome":
            pid = data.get("participant_id")
            if pid:
                self._my_participant_ids.add(pid)
                logger.info("ws.welcome", room_id=room_id, participant_id=pid)
            # Issue #61 — cache the agent identity the server assigned
            # to this connection so ``should_respond`` can gate
            # ``room_query`` forwarding. Only overwrite if the server
            # sent a value: an agent reconnecting through a room that
            # another session already populated must not clear it.
            aid = data.get("agent_id")
            if aid:
                self._agent_id = aid
            # Issue #148 Part 3 — refresh the opt-out cache on every
            # welcome. Absent field (older servers, non-agent sessions)
            # leaves the default False in place, which preserves the
            # pre-#148 ingest behaviour exactly.
            if "context_window_opt_out" in data:
                self._context_window_opt_out = bool(
                    data.get("context_window_opt_out")
                )
            # Issue #159 Phase A — cache the room's speaker-strategy
            # fields so ``decide_policy`` can dispatch on them. Default
            # 'mentioned_only' keeps pre-#159 rooms on the legacy path.
            self._speaker_strategy[room_id] = data.get(
                "speaker_strategy", "mentioned_only"
            )
            self._orchestrator_agent_id[room_id] = data.get(
                "orchestrator_agent_id"
            )
            self._next_speaker_participant_id[room_id] = data.get(
                "next_speaker_participant_id"
            )
            # The server may include rooms that were added while we
            # were disconnected. Join any we don't already have.
            for pending in data.get("pending_rooms") or []:
                if pending not in self._tasks:
                    logger.info("ws.pending_room_join", room_id=pending, via=room_id)
                    await self.join_room(pending)
        elif msg_type == "join_room":
            new_room = data.get("room_id")
            if new_room and new_room not in self._tasks:
                logger.info("ws.dynamic_join", room_id=new_room, via=room_id)
                await self.join_room(new_room)
        elif msg_type == "error":
            logger.warning("ws.server_error", detail=data.get("detail"))

    async def _room_loop(self, room_id: str) -> None:
        """Reconnection loop with exponential backoff + since_seq recovery."""
        delay = 1.0
        while True:
            try:
                since = self._last_seq.get(room_id, 0)
                ws_url = f"{self._server_url}/ws/rooms/{room_id}"
                if since > 0:
                    ws_url += f"?since_seq={since}"

                subprotocols = build_subprotocols(self._token)

                # Issue #190 — codex turns can legitimately run 5+
                # minutes while the SDK waits on tool chains, and the
                # ``websockets`` library default
                # ``ping_interval=20, ping_timeout=20`` closed the
                # connection mid-turn (``sent 1011 keepalive ping
                # timeout``). The adapter produced a full response
                # from ``thread.run_text`` but the subsequent
                # ``client.send`` hit a closed socket, silently
                # dropping the answer. We still ping periodically so
                # a dead agent is detectable, but the timeout has to
                # tolerate the adapter's turn cap
                # (``_CODEX_TURN_TIMEOUT = 600s``) plus tool-call
                # reasoning slack.
                async with ws_connect(
                    ws_url,
                    subprotocols=subprotocols,
                    ping_interval=60,
                    ping_timeout=600,
                ) as ws:
                    self._connections[room_id] = ws
                    delay = 1.0  # Reset backoff on successful connect
                    logger.info("ws.connected", room_id=room_id, agent=self._agent_name)

                    # Notify join handlers
                    for handler in self._join_handlers:
                        try:
                            await handler(room_id)
                        except Exception as exc:
                            logger.error("handler.join_error", error=str(exc))

                    # Read messages — welcome, join_room, message,
                    # error are all dispatched through _process_frame.
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        try:
                            data = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            logger.warning("ws.bad_frame", length=len(raw) if raw else 0)
                            continue

                        await self._process_frame(room_id, data)

            except websockets.exceptions.InvalidStatusCode as exc:
                # 403/4003 = not a member of this room. Don't retry —
                # the agent was either not invited or was removed.
                if getattr(exc, "status_code", 0) in (403, 4003):
                    logger.warning("ws.not_member_giving_up", room_id=room_id)
                    self._tasks.pop(room_id, None)
                    return
                logger.warning("ws.disconnected", room_id=room_id, error=str(exc), retry_in=delay)
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidURI,
                OSError,
            ) as exc:
                logger.warning("ws.disconnected", room_id=room_id, error=str(exc), retry_in=delay)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("ws.unexpected_error", room_id=room_id, error=str(exc))
            finally:
                self._connections.pop(room_id, None)

            if not self._running:
                break

            # Exponential backoff
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_reconnect_delay)

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http
