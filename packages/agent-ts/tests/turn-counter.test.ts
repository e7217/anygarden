// Turn-counter tests. Ported 1:1 from
// ``packages/agent/tests/test_client.py`` ``TestIsTaskInitContent`` and
// ``TestAgentTurnCounter``.
//
// The #67 regression — ``test_agent_only_room_query_fanout_regression``
// — is the load-bearing test. Without the task-init reset across all
// three counter paths the last "reply N" frame is silently dropped by
// the turn limit.

import { describe, it, expect } from "vitest";
import { ChatClient } from "../src/client.js";
import {
  TurnCounter,
  isTaskInitContent,
  DEFAULT_MAX_AGENT_TURNS,
} from "../src/routing/turn-counter.js";
import type { MessageOut } from "../src/protocol/frames.js";

describe("isTaskInitContent", () => {
  it("identifies [ROOM_QUERY] prefix", () => {
    expect(isTaskInitContent("[ROOM_QUERY] what's the plan?")).toBe(true);
  });
  it("identifies [DELEGATED] prefix", () => {
    expect(isTaskInitContent("[DELEGATED] please summarise")).toBe(true);
  });
  it("rejects regular content", () => {
    expect(isTaskInitContent("hello, team")).toBe(false);
  });
  it("rejects empty string", () => {
    expect(isTaskInitContent("")).toBe(false);
  });
  it("rejects prefix that is not at start", () => {
    expect(isTaskInitContent("fyi [ROOM_QUERY] embedded")).toBe(false);
  });
});

describe("TurnCounter — unit behaviour", () => {
  it("defaults to DEFAULT_MAX_AGENT_TURNS", () => {
    const tc = new TurnCounter();
    expect(tc.max).toBe(DEFAULT_MAX_AGENT_TURNS);
  });

  it("self regular message increments", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 2);
    const d = tc.handleSelf("r", "hello");
    expect(d.outcome).toBe("skip_self");
    expect(d.count).toBe(3);
  });

  it("self [ROOM_QUERY] resets (#67)", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 5);
    const d = tc.handleSelf("r", "[ROOM_QUERY] forwarded");
    expect(d.count).toBe(0);
  });

  it("self [DELEGATED] resets (#67)", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 4);
    const d = tc.handleSelf("r", "[DELEGATED] do this");
    expect(d.count).toBe(0);
  });

  it("nonce-echo regular message increments", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 1);
    const d = tc.handleNonceEcho("r", "regular");
    expect(d.count).toBe(2);
  });

  it("nonce-echo [ROOM_QUERY] resets (#67)", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 5);
    const d = tc.handleNonceEcho("r", "[ROOM_QUERY] ask");
    expect(d.count).toBe(0);
  });

  it("incoming agent message increments, dispatches until limit", () => {
    const tc = new TurnCounter(3);
    tc.setCount("r", 0);
    for (let i = 0; i < 3; i++) {
      const d = tc.handleIncoming("r", `msg ${i}`, true);
      expect(d.outcome).toBe("deliver");
    }
    // 4th exceeds
    const over = tc.handleIncoming("r", "over", true);
    expect(over.outcome).toBe("skip_limit");
  });

  it("incoming human message resets to 0 and delivers", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 5);
    const d = tc.handleIncoming("r", "hi from human", false);
    expect(d.outcome).toBe("deliver");
    expect(d.count).toBe(0);
  });

  it("incoming [ROOM_QUERY] from another agent resets and delivers (#67)", () => {
    const tc = new TurnCounter();
    tc.setCount("r", 5);
    const d = tc.handleIncoming("r", "[ROOM_QUERY] q", true);
    expect(d.outcome).toBe("deliver");
    expect(d.count).toBe(0);
  });
});

// ── #67 regression exercised against ChatClient end-to-end ───────────

function makeMsg(overrides: Partial<MessageOut>): MessageOut {
  return {
    type: "message",
    id: "m",
    room_id: "room-a",
    participant_id: "other-agent-pid",
    content: "hello",
    seq: 1,
    created_at: "2026-04-16T00:00:00Z",
    metadata: null,
    ...overrides,
  };
}

function makeClient() {
  const c = new ChatClient({ serverUrl: "ws://x", token: "t" });
  c.myParticipantIds.add("self-pid");
  return c;
}

describe("ChatClient — turn counter paths (#67 regression)", () => {
  it("self regular message bumps count and drops the frame", async () => {
    const c = makeClient();
    const handler = (_m: MessageOut) => {
      throw new Error("should not be called");
    };
    c.onMessage(handler);
    // Seed internal counter directly so we can assert it post-hoc.
    await c.__testFeedFrame(
      "room-a",
      makeMsg({ participant_id: "self-pid", content: "hello" }),
    );
    // No assertion error thrown → handler was not invoked. Count
    // bookkeeping is covered by the unit test above.
  });

  it("self [ROOM_QUERY] resets counter via hard-filter path (#67)", async () => {
    const c = makeClient();
    // Prime counter to near-limit via a fake internal write; we use
    // the public TurnCounter via the exposed field for verification.
    const tc = (c as unknown as { turnCounter: TurnCounter }).turnCounter;
    tc.setCount("room-a", 5);
    await c.__testFeedFrame(
      "room-a",
      makeMsg({
        participant_id: "self-pid",
        content: "[ROOM_QUERY] forwarded question",
      }),
    );
    expect(tc.getCount("room-a")).toBe(0);
  });

  it("nonce-echo [ROOM_QUERY] resets counter via soft-filter path (#67)", async () => {
    const c = makeClient();
    // Send a [ROOM_QUERY] so the client allocates a nonce; we then
    // simulate the echo arriving with ``participant_id`` that the
    // hard filter would miss (different pid).
    const sent: string[] = [];
    c.__testSetConnection("room-a", {
      send: (data: string) => sent.push(data),
      close: () => {},
      on: () => {},
    });
    await c.send("room-a", "[ROOM_QUERY] ask other room");
    const nonce = JSON.parse(sent[0]).metadata._nonce;

    const tc = (c as unknown as { turnCounter: TurnCounter }).turnCounter;
    tc.setCount("room-a", 5);
    await c.__testFeedFrame(
      "room-a",
      makeMsg({
        participant_id: "ghost-pid",
        content: "[ROOM_QUERY] ask other room",
        metadata: { _nonce: nonce },
      }),
    );
    expect(tc.getCount("room-a")).toBe(0);
  });

  it("agent-only room: 4 rounds of [ROOM_QUERY] + reply all dispatch (#67 regression)", async () => {
    const c = makeClient();
    // Tighter bound to force the regression under test — the Python
    // equivalent sets max to 3 for the same reason. Swap the client's
    // internal counter for a shorter-bound one.
    const tight = new TurnCounter(3);
    (c as unknown as { turnCounter: TurnCounter }).turnCounter = tight;

    const calls: string[] = [];
    c.onMessage((m) => {
      calls.push(m.content);
    });

    const frames: MessageOut[] = [
      // round 1
      makeMsg({ seq: 1, participant_id: "self-pid", content: "[ROOM_QUERY] q1", metadata: null }),
      makeMsg({ seq: 2, participant_id: "other-pid", content: "reply 1", metadata: { _nonce: "f1" } }),
      // round 2
      makeMsg({ seq: 3, participant_id: "self-pid", content: "[ROOM_QUERY] q2", metadata: null }),
      makeMsg({ seq: 4, participant_id: "other-pid", content: "reply 2", metadata: { _nonce: "f2" } }),
      // round 3
      makeMsg({ seq: 5, participant_id: "self-pid", content: "[ROOM_QUERY] q3", metadata: null }),
      makeMsg({ seq: 6, participant_id: "other-pid", content: "reply 3", metadata: { _nonce: "f3" } }),
      // round 4 — without the fix this reply would be dropped by the limit
      makeMsg({ seq: 7, participant_id: "self-pid", content: "[ROOM_QUERY] q4", metadata: null }),
      makeMsg({ seq: 8, participant_id: "other-pid", content: "reply 4", metadata: { _nonce: "f4" } }),
    ];
    for (const f of frames) {
      await c.__testFeedFrame("room-a", f);
    }
    expect(calls).toEqual(["reply 1", "reply 2", "reply 3", "reply 4"]);
  });
});
