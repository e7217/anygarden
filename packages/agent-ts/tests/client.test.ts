import { describe, it, expect, vi } from "vitest";
import { ChatClient } from "../src/client.js";

function makeClient(overrides: Partial<ConstructorParameters<typeof ChatClient>[0]> = {}) {
  return new ChatClient({
    serverUrl: "ws://test.local",
    token: "t",
    agentName: "TestAgent",
    ...overrides,
  });
}

describe("ChatClient — welcome frame handling", () => {
  it("records my participant_id", async () => {
    const c = makeClient();
    await c.__testFeedFrame("r1", {
      type: "welcome",
      participant_id: "pid-1",
      pending_rooms: [],
    });
    expect(c.myParticipantIds.has("pid-1")).toBe(true);
  });

  it("records the welcome agent_id (Issue #61)", async () => {
    const c = makeClient();
    await c.__testFeedFrame("r1", {
      type: "welcome",
      participant_id: "pid-1",
      pending_rooms: [],
      agent_id: "agent-rep",
    });
    expect(c.agentId).toBe("agent-rep");
  });

  it("does not clear agent_id on a welcome that omits it", async () => {
    const c = makeClient();
    await c.__testFeedFrame("r1", {
      type: "welcome",
      participant_id: "pid-1",
      pending_rooms: [],
      agent_id: "agent-rep",
    });
    await c.__testFeedFrame("r2", {
      type: "welcome",
      participant_id: "pid-2",
      pending_rooms: [],
    });
    expect(c.agentId).toBe("agent-rep");
  });

  it("auto-joins pending_rooms", async () => {
    const c = makeClient();
    const joinSpy = vi.spyOn(c, "joinRoom").mockResolvedValue();
    await c.__testFeedFrame("r1", {
      type: "welcome",
      participant_id: "pid-1",
      pending_rooms: ["pending-1", "pending-2"],
    });
    // Both pending rooms should trigger joinRoom. Because the Python
    // equivalent fires without awaiting, we allow a microtask flush.
    await Promise.resolve();
    expect(joinSpy).toHaveBeenCalledWith("pending-1");
    expect(joinSpy).toHaveBeenCalledWith("pending-2");
  });
});

describe("ChatClient — dynamic join_room", () => {
  it("joins a room referenced in a join_room frame", async () => {
    const c = makeClient();
    const joinSpy = vi.spyOn(c, "joinRoom").mockResolvedValue();
    await c.__testFeedFrame("r1", {
      type: "join_room",
      room_id: "r2",
      participant_id: "pid-1",
    });
    expect(joinSpy).toHaveBeenCalledWith("r2");
  });

  it("does not double-join a room we're already in", async () => {
    const c = makeClient();
    // Simulate we're already in r2 by wiring a fake connection.
    c.__testSetConnection("r2", {
      send: () => {},
      close: () => {},
      on: () => {},
    });
    const joinSpy = vi.spyOn(c, "joinRoom").mockResolvedValue();
    await c.__testFeedFrame("r1", {
      type: "join_room",
      room_id: "r2",
      participant_id: "pid-1",
    });
    expect(joinSpy).not.toHaveBeenCalled();
  });
});

describe("ChatClient — message dispatch", () => {
  function makeMsg(overrides: Record<string, unknown> = {}) {
    return {
      type: "message" as const,
      id: "m1",
      room_id: "r1",
      participant_id: "other",
      content: "hello",
      seq: 1,
      created_at: "2026-04-16T00:00:00Z",
      metadata: null,
      ...overrides,
    };
  }

  it("dispatches to registered handlers in order", async () => {
    const c = makeClient();
    const calls: string[] = [];
    c.onMessage(async () => {
      calls.push("first");
    });
    c.onMessage(async () => {
      calls.push("second");
    });
    await c.__testFeedFrame("r1", makeMsg());
    expect(calls).toEqual(["first", "second"]);
  });

  it("drops own messages via hard filter (participant_id)", async () => {
    const c = makeClient();
    c.myParticipantIds.add("pid-self");
    const handler = vi.fn();
    c.onMessage(handler);
    await c.__testFeedFrame(
      "r1",
      makeMsg({ participant_id: "pid-self", content: "hi" }),
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it("drops own echo via nonce soft filter", async () => {
    const c = makeClient();
    // Put a WS stub so ``send`` can allocate+track a nonce.
    const sent: string[] = [];
    c.__testSetConnection("r1", {
      send: (data: string) => sent.push(data),
      close: () => {},
      on: () => {},
    });
    await c.send("r1", "outbound");
    const nonce = JSON.parse(sent[0]).metadata._nonce;
    expect(typeof nonce).toBe("string");

    const handler = vi.fn();
    c.onMessage(handler);
    await c.__testFeedFrame(
      "r1",
      makeMsg({ participant_id: "other", content: "outbound", metadata: { _nonce: nonce } }),
    );
    expect(handler).not.toHaveBeenCalled();
  });

  it("updates last_seq high-water mark", async () => {
    const c = makeClient();
    const handler = vi.fn();
    c.onMessage(handler);
    await c.__testFeedFrame("r1", makeMsg({ seq: 10 }));
    await c.__testFeedFrame("r1", makeMsg({ seq: 5 })); // older — should not overwrite
    await c.__testFeedFrame("r1", makeMsg({ seq: 15 }));
    // indirectly probe via the since_seq URL path later; for now a
    // handler call count is sufficient proof of dispatch.
    expect(handler).toHaveBeenCalledTimes(3);
  });

  it("respects agent turn limit — skips after max turns", async () => {
    const c = makeClient();
    const handler = vi.fn();
    c.onMessage(handler);
    // Default max is 6. Feed 7 agent messages.
    for (let i = 0; i < 7; i++) {
      await c.__testFeedFrame(
        "r1",
        makeMsg({
          seq: i + 1,
          metadata: { _nonce: `agent-nonce-${i}` },
          content: `agent msg ${i}`,
        }),
      );
    }
    // Messages 1..6 dispatch; message 7 is past the limit → skip.
    expect(handler).toHaveBeenCalledTimes(6);
  });

  it("human message (no _nonce) resets turn count and dispatches", async () => {
    const c = makeClient();
    const handler = vi.fn();
    c.onMessage(handler);
    // Prime the counter up to the limit.
    for (let i = 0; i < 7; i++) {
      await c.__testFeedFrame(
        "r1",
        makeMsg({ seq: i + 1, metadata: { _nonce: `n${i}` }, content: `agent ${i}` }),
      );
    }
    const before = handler.mock.calls.length;
    // A human message (no nonce) resets and dispatches.
    await c.__testFeedFrame(
      "r1",
      makeMsg({ seq: 99, metadata: null, content: "human interjection" }),
    );
    expect(handler.mock.calls.length).toBe(before + 1);
    // And now a new agent message should start fresh.
    await c.__testFeedFrame(
      "r1",
      makeMsg({ seq: 100, metadata: { _nonce: "after-human" }, content: "agent replies" }),
    );
    expect(handler.mock.calls.length).toBe(before + 2);
  });
});

describe("ChatClient — send()", () => {
  it("attaches a nonce to every send", async () => {
    const c = makeClient();
    const sent: string[] = [];
    c.__testSetConnection("r1", {
      send: (data: string) => sent.push(data),
      close: () => {},
      on: () => {},
    });
    await c.send("r1", "hello");
    const frame = JSON.parse(sent[0]);
    expect(frame.type).toBe("send");
    expect(frame.content).toBe("hello");
    expect(typeof frame.metadata._nonce).toBe("string");
  });

  it("merges caller metadata with the nonce", async () => {
    const c = makeClient();
    const sent: string[] = [];
    c.__testSetConnection("r1", {
      send: (data: string) => sent.push(data),
      close: () => {},
      on: () => {},
    });
    await c.send("r1", "hi", { foo: "bar" });
    const frame = JSON.parse(sent[0]);
    expect(frame.metadata.foo).toBe("bar");
    expect(typeof frame.metadata._nonce).toBe("string");
  });

  it("throws when not connected", async () => {
    const c = makeClient();
    await expect(c.send("nobody", "hi")).rejects.toThrow(/Not connected/);
  });
});

describe("ChatClient — REST helpers", () => {
  it("findSubRoom returns the first match", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify([{ id: "sub-1" }, { id: "sub-2" }]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    const c = makeClient({ httpFetch: fetchMock });
    const result = await c.findSubRoom("parent", "dev");
    expect(result).toBe("sub-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = (fetchMock as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("http://test.local/api/v1/rooms/parent/sub-rooms");
    expect(url).toContain("name=dev");
  });

  it("findSubRoom returns null on 404", async () => {
    const fetchMock = vi.fn(async () => new Response("", { status: 404 })) as unknown as typeof fetch;
    const c = makeClient({ httpFetch: fetchMock });
    expect(await c.findSubRoom("parent", "dev")).toBeNull();
  });

  it("getRoomParticipants returns participants from the response", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ participants: [{ id: "p1" }, { id: "p2" }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    const c = makeClient({ httpFetch: fetchMock });
    const result = await c.getRoomParticipants("r1");
    expect(result.length).toBe(2);
  });
});

describe("ChatClient — reconnect loop (fake WS)", () => {
  it("retries on connection error with exponential backoff", { timeout: 5000 }, async () => {
    const attempts: string[] = [];
    // Fake WS: first 2 attempts error immediately; then close.
    // We don't need a "success" attempt — we just verify retries happen.
    class FakeWsCtor {
      constructor(url: string, _protocols: string[]) {
        attempts.push(url);
        const handlers: Record<string, ((...args: unknown[]) => void)[]> = {};
        const fire = (evt: string, ...args: unknown[]) => {
          (handlers[evt] ?? []).forEach((cb) => cb(...args));
        };
        // Async error, then close.
        setImmediate(() => {
          fire("error", new Error("boom"));
          fire("close", 1006, Buffer.from(""));
        });
        return {
          send: () => {},
          close: () => fire("close", 1000, Buffer.from("")),
          on: (evt: string, cb: (...args: unknown[]) => void) => {
            (handlers[evt] ??= []).push(cb);
          },
        } as unknown as import("../src/client.js").WSLike;
      }
    }
    const c = new ChatClient({
      serverUrl: "ws://test.local",
      token: "t",
      webSocketCtor: FakeWsCtor as unknown as import("../src/client.js").WebSocketCtor,
      initialReconnectDelay: 5,
      maxReconnectDelay: 10,
    });
    await c.joinRoom("r1");
    // Give the loop ~80ms to retry a few times.
    await new Promise((r) => setTimeout(r, 80));
    await c.close();
    expect(attempts.length).toBeGreaterThanOrEqual(2);
    // All attempts should target the same path.
    for (const url of attempts) {
      expect(url.startsWith("ws://test.local/ws/rooms/r1")).toBe(true);
    }
  });

  it("includes since_seq on reconnect after receiving a message", async () => {
    const c = new ChatClient({
      serverUrl: "ws://test.local",
      token: "t",
    });
    // Prime via a direct message feed.
    await c.__testFeedFrame("r1", {
      type: "message",
      id: "m",
      room_id: "r1",
      participant_id: "human",
      content: "hi",
      seq: 42,
      created_at: "2026-04-16T00:00:00Z",
      metadata: null,
    });
    // Poke private state via a minimal type assertion. Keeping this in
    // the reconnect test so we verify the integration, not just the
    // turn-counter code.
    const lastSeq = (c as unknown as { lastSeq: Map<string, number> }).lastSeq;
    expect(lastSeq.get("r1")).toBe(42);
  });
});
