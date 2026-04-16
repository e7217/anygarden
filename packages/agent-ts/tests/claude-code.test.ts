// Unit tests for ClaudeCodeAdapter. The real SDK is stubbed via the
// ``sdkLoader`` option so these tests are hermetic — no network, no
// native binding load, no API credentials.
//
// Live smoke-test steps (manual) are documented in README.md, phase 7.

import { describe, it, expect, vi } from "vitest";
import { ClaudeCodeAdapter, type SDKModule, type SDKSession } from "../src/engines/claude-code.js";
import type { MessageOut } from "../src/protocol/frames.js";

function makeSession(stream: unknown[]): SDKSession {
  let sessionId: string | undefined;
  const session: SDKSession = {
    get sessionId() {
      if (!sessionId) throw new Error("not initialised");
      return sessionId;
    },
    async send(_msg: string) {
      sessionId ??= "sid-from-send";
    },
    stream() {
      return (async function* () {
        for (const m of stream) yield m as Record<string, unknown>;
      })() as AsyncIterable<Record<string, unknown>>;
    },
    close() {},
  } as unknown as SDKSession;
  return session;
}

function makeSdk(firstStream: unknown[], secondStream?: unknown[]) {
  const calls = { create: 0, resume: 0 };
  const sdk: SDKModule = {
    unstable_v2_createSession: () => {
      calls.create += 1;
      return makeSession(firstStream);
    },
    unstable_v2_resumeSession: () => {
      calls.resume += 1;
      return makeSession(secondStream ?? firstStream);
    },
  };
  return { sdk, calls };
}

function makeMsg(overrides: Partial<MessageOut> = {}): MessageOut {
  return {
    type: "message",
    id: "m",
    room_id: "room-a",
    participant_id: "human",
    content: "hello",
    seq: 1,
    created_at: "2026-04-16T00:00:00Z",
    metadata: null,
    ...overrides,
  };
}

describe("ClaudeCodeAdapter", () => {
  it("returns null when SDK fails to load", async () => {
    const adapter = new ClaudeCodeAdapter({
      sdkLoader: async () => {
        throw new Error("no SDK");
      },
    });
    await adapter.start();
    const reply = await adapter.onMessage(makeMsg());
    expect(reply).toBeNull();
  });

  it("prefers ResultMessage.result when available", async () => {
    const { sdk } = makeSdk([
      {
        type: "assistant",
        message: { content: [{ type: "text", text: "streaming..." }] },
      },
      { type: "result", result: "final answer" },
    ]);
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    const reply = await adapter.onMessage(makeMsg());
    expect(reply).toBe("final answer");
  });

  it("falls back to concatenated TextBlocks when no result field", async () => {
    const { sdk } = makeSdk([
      {
        type: "assistant",
        message: {
          content: [
            { type: "text", text: "first" },
            { type: "tool_use", id: "t1" }, // should be filtered
            { type: "text", text: "second" },
          ],
        },
      },
    ]);
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    const reply = await adapter.onMessage(makeMsg());
    expect(reply).toBe("first\n\nsecond");
  });

  it("returns null for an empty stream", async () => {
    const { sdk } = makeSdk([]);
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    expect(await adapter.onMessage(makeMsg())).toBeNull();
  });

  it("reuses the live session for follow-up turns in the same room", async () => {
    // Two turns within one adapter lifecycle should share the session.
    // First turn: createSession is called; second turn: the same
    // session object is reused (no create, no resume). This mirrors
    // the Python runtime's in-process ``resume=sid`` behaviour which
    // the V2 API subsumes by giving us a live session handle.
    const session = makeSession([{ type: "result", result: "answer" }]);
    let creates = 0;
    let resumes = 0;
    const sdk: SDKModule = {
      unstable_v2_createSession: () => {
        creates += 1;
        return session;
      },
      unstable_v2_resumeSession: () => {
        resumes += 1;
        return session;
      },
    };
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    await adapter.onMessage(makeMsg({ content: "q1" }));
    await adapter.onMessage(makeMsg({ content: "q2" }));
    expect(creates).toBe(1);
    expect(resumes).toBe(0);
  });

  it("creates a fresh session per room (isolated sessions)", async () => {
    const session = makeSession([{ type: "result", result: "answer" }]);
    let creates = 0;
    const sdk: SDKModule = {
      unstable_v2_createSession: () => {
        creates += 1;
        return session;
      },
      unstable_v2_resumeSession: () => session,
    };
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    await adapter.onMessage(makeMsg({ room_id: "room-a", content: "q" }));
    await adapter.onMessage(makeMsg({ room_id: "room-b", content: "q" }));
    expect(creates).toBe(2);
  });

  it("invokes onStreamChunk for every assistant text block", async () => {
    const { sdk } = makeSdk([
      {
        type: "assistant",
        message: {
          content: [
            { type: "text", text: "chunk a" },
            { type: "text", text: "chunk b" },
          ],
        },
      },
      { type: "result", result: "final" },
    ]);
    const chunks: string[] = [];
    const adapter = new ClaudeCodeAdapter({
      sdkLoader: async () => sdk,
      onStreamChunk: (t) => chunks.push(t),
    });
    await adapter.start();
    await adapter.onMessage(makeMsg());
    expect(chunks).toEqual(["chunk a", "chunk b"]);
  });

  it("skips empty/whitespace text blocks", async () => {
    const { sdk } = makeSdk([
      {
        type: "assistant",
        message: {
          content: [
            { type: "text", text: "  " },
            { type: "text", text: "real" },
          ],
        },
      },
    ]);
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    const reply = await adapter.onMessage(makeMsg());
    expect(reply).toBe("real");
  });

  it("returns null when called with empty content", async () => {
    const { sdk } = makeSdk([{ type: "result", result: "x" }]);
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    const reply = await adapter.onMessage(makeMsg({ content: "" }));
    expect(reply).toBeNull();
  });

  it("stop() closes sessions and prevents further queries", async () => {
    const closeSpy = vi.fn();
    const sdk: SDKModule = {
      unstable_v2_createSession: () =>
        ({
          sessionId: "sid",
          send: async () => {},
          stream: () =>
            (async function* () {
              yield { type: "result", result: "x" };
            })(),
          close: closeSpy,
        }) as unknown as SDKSession,
      unstable_v2_resumeSession: () => {
        throw new Error("should not resume");
      },
    };
    const adapter = new ClaudeCodeAdapter({ sdkLoader: async () => sdk });
    await adapter.start();
    await adapter.onMessage(makeMsg());
    await adapter.stop();
    expect(closeSpy).toHaveBeenCalled();
    const reply = await adapter.onMessage(makeMsg({ content: "after stop" }));
    expect(reply).toBeNull();
  });
});
