import { describe, it, expect, vi } from "vitest";
import { ChatClient } from "../src/client.js";
import {
  parseRoomQuery,
  executeRoomQuery,
  stripRoomMention,
  fmtAgo,
} from "../src/coordination/room-query.js";
import type { MessageOut } from "../src/protocol/frames.js";

describe("parseRoomQuery", () => {
  it("returns null when metadata has no room_query", () => {
    const msg = makeMsg({ metadata: { mentions: [] } });
    expect(parseRoomQuery(msg)).toBeNull();
  });

  it("extracts the core fields", () => {
    const msg = makeMsg({
      content: "<#room:xyz> 의견?",
      metadata: {
        room_query: {
          target_room_id: "target-1",
          source_room_id: "source-1",
          query_id: "q-1",
          source_participant_id: "human-pid",
        },
      },
    });
    const q = parseRoomQuery(msg);
    expect(q).toEqual({
      targetRoomId: "target-1",
      sourceRoomId: "source-1",
      content: "<#room:xyz> 의견?",
      queryId: "q-1",
      sourceParticipantId: "human-pid",
    });
  });

  it("tolerates missing optional fields (legacy pre-#55)", () => {
    const msg = makeMsg({
      metadata: {
        room_query: { target_room_id: "t", source_room_id: "s" },
      },
    });
    const q = parseRoomQuery(msg);
    expect(q?.queryId).toBe("");
    expect(q?.sourceParticipantId).toBeNull();
  });
});

describe("stripRoomMention", () => {
  it("removes <#room:…> tokens", () => {
    expect(stripRoomMention("<#room:abc> question")).toBe("question");
    expect(stripRoomMention("hi <#room:abc> there")).toBe("hi there");
  });

  it("collapses leftover whitespace", () => {
    expect(stripRoomMention("<#room:abc>   padded")).toBe("padded");
  });
});

describe("fmtAgo", () => {
  const now = new Date("2026-04-16T12:00:00Z");
  it("returns 방금 전 for <60s", () => {
    expect(fmtAgo("2026-04-16T11:59:30Z", now)).toBe("방금 전");
  });
  it("returns N분 전 for <1h", () => {
    expect(fmtAgo("2026-04-16T11:30:00Z", now)).toBe("30분 전");
  });
  it("returns N시간 전 for <24h", () => {
    expect(fmtAgo("2026-04-16T05:00:00Z", now)).toBe("7시간 전");
  });
  it("returns N일 전 for >=24h", () => {
    expect(fmtAgo("2026-04-14T12:00:00Z", now)).toBe("2일 전");
  });
  it("returns 알 수 없음 on missing/bad input", () => {
    expect(fmtAgo(null, now)).toBe("알 수 없음");
    expect(fmtAgo("not a date", now)).toBe("알 수 없음");
  });
});

// ── executeRoomQuery — solo / completed / timeout paths ─────────────

function makeMsg(overrides: Partial<MessageOut> = {}): MessageOut {
  return {
    type: "message",
    id: "m",
    room_id: "source-1",
    participant_id: "human-pid",
    content: "<#room:target-1> 의견?",
    seq: 1,
    created_at: "2026-04-16T00:00:00Z",
    metadata: null,
    ...overrides,
  };
}

describe("executeRoomQuery", () => {
  it("delivers solo status when target room has no online agents", async () => {
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    vi.spyOn(c, "getRoomParticipants").mockResolvedValue([
      // A human — not an agent, doesn't count.
      { id: "human-1", kind: "user" } as Record<string, unknown>,
    ]);
    const sent: Array<{ roomId: string; content: string; metadata?: Record<string, unknown> | null }> =
      [];
    vi.spyOn(c, "send").mockImplementation(async (roomId, content, metadata) => {
      sent.push({ roomId, content, metadata });
    });

    await executeRoomQuery(
      c,
      makeMsg(),
      {
        targetRoomId: "target-1",
        sourceRoomId: "source-1",
        content: "<#room:target-1> 질문?",
        queryId: "q1",
        sourceParticipantId: "human-pid",
      },
      { joinSettleMs: 0 },
    );

    expect(sent.length).toBe(1);
    expect(sent[0].roomId).toBe("source-1");
    expect(sent[0].content).toContain("[취합 결과]");
    expect(sent[0].content).toContain("대상 방에 응답할 에이전트가 없음");
    const result = sent[0].metadata?.room_query_result as Record<string, unknown>;
    expect(result.status).toBe("solo");
    expect(result.expected).toBe(0);
  });

  it("forwards question and delivers completed summary when all reply", async () => {
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    vi.spyOn(c, "getRoomParticipants").mockResolvedValue([
      { id: "agent-a", kind: "agent", online: true, display_name: "Alice" },
      { id: "agent-b", kind: "agent", online: true, display_name: "Bob" },
    ] as unknown as Record<string, unknown>[]);
    const sent: Array<{ roomId: string; content: string; metadata?: Record<string, unknown> | null }> =
      [];
    vi.spyOn(c, "send").mockImplementation(async (roomId, content, metadata) => {
      sent.push({ roomId, content, metadata });
    });

    await executeRoomQuery(
      c,
      makeMsg(),
      {
        targetRoomId: "target-1",
        sourceRoomId: "source-1",
        content: "<#room:target-1> 질문?",
        queryId: "q1",
        sourceParticipantId: "human-pid",
      },
      { joinSettleMs: 0 },
    );
    // 1st send = forward to target room
    expect(sent[0].roomId).toBe("target-1");
    expect(sent[0].content).toBe("[ROOM_QUERY] 질문?");
    expect(sent[0].metadata?.room_query_forward).toBeDefined();

    // Simulate two replies.
    await c.__testFeedFrame(
      "target-1",
      makeMsg({
        id: "r1",
        room_id: "target-1",
        participant_id: "agent-a",
        content: "alice says X",
        metadata: { _nonce: "n1" },
      }),
    );
    await c.__testFeedFrame(
      "target-1",
      makeMsg({
        id: "r2",
        room_id: "target-1",
        participant_id: "agent-b",
        content: "bob says Y",
        metadata: { _nonce: "n2" },
      }),
    );
    // Flush any pending microtasks.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const result = sent.find((s) => s.roomId === "source-1");
    expect(result).toBeDefined();
    expect(result!.content).toContain("[취합 결과] (2/2명 응답)");
    expect(result!.content).toContain("alice says X");
    expect(result!.content).toContain("bob says Y");
    expect(result!.metadata?.room_query_result).toMatchObject({
      status: "completed",
      responded: 2,
      expected: 2,
    });
  });

  it("emits timeout delivery with partial collected responses", async () => {
    vi.useFakeTimers();
    try {
      const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
      vi.spyOn(c, "isConnected").mockReturnValue(true);
      vi.spyOn(c, "getRoomParticipants").mockResolvedValue([
        { id: "agent-a", kind: "agent", online: true, display_name: "Alice" },
        { id: "agent-b", kind: "agent", online: true, display_name: "Bob" },
      ] as unknown as Record<string, unknown>[]);
      const sent: Array<{
        roomId: string;
        content: string;
        metadata?: Record<string, unknown> | null;
      }> = [];
      vi.spyOn(c, "send").mockImplementation(async (roomId, content, metadata) => {
        sent.push({ roomId, content, metadata });
      });

      await executeRoomQuery(
        c,
        makeMsg(),
        {
          targetRoomId: "target-1",
          sourceRoomId: "source-1",
          content: "<#room:target-1> 질문?",
          queryId: "q1",
          sourceParticipantId: "human-pid",
        },
        { timeoutMs: 50, joinSettleMs: 0 },
      );
      // Only one agent replies.
      await c.__testFeedFrame(
        "target-1",
        makeMsg({
          id: "r1",
          room_id: "target-1",
          participant_id: "agent-a",
          content: "alice alone",
          metadata: { _nonce: "n1" },
        }),
      );

      // Fast-forward past the timeout.
      await vi.advanceTimersByTimeAsync(60);
      // Let deliverResult ``send`` resolve.
      await Promise.resolve();

      const timeoutEntry = sent.find(
        (s) => s.roomId === "source-1" && (s.metadata?.room_query_result as { status?: string })?.status === "timeout",
      );
      expect(timeoutEntry).toBeDefined();
      expect(timeoutEntry!.content).toContain("[취합 결과] (1/2명 응답)");
      expect(timeoutEntry!.content).toContain("1명 미응답");
      expect(timeoutEntry!.content).toContain("alice alone");
      expect(timeoutEntry!.content).toContain("- Bob (응답 없음)");
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores our own [ROOM_QUERY] echo in the multi-reply callback", async () => {
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    vi.spyOn(c, "getRoomParticipants").mockResolvedValue([
      { id: "agent-a", kind: "agent", online: true, display_name: "Alice" },
    ] as unknown as Record<string, unknown>[]);
    const sent: Array<{ roomId: string; content: string; metadata?: Record<string, unknown> | null }> =
      [];
    vi.spyOn(c, "send").mockImplementation(async (roomId, content, metadata) => {
      sent.push({ roomId, content, metadata });
    });

    await executeRoomQuery(
      c,
      makeMsg(),
      {
        targetRoomId: "target-1",
        sourceRoomId: "source-1",
        content: "<#room:target-1> 질문?",
        queryId: "q1",
        sourceParticipantId: null,
      },
      { joinSettleMs: 0 },
    );
    // Ghost participant id — would slip past hard filter. Content is
    // [ROOM_QUERY] so it must be ignored as our own broadcast.
    await c.__testFeedFrame(
      "target-1",
      makeMsg({
        id: "ghost",
        room_id: "target-1",
        participant_id: "ghost-pid",
        content: "[ROOM_QUERY] echoing myself",
        metadata: { _nonce: "ghost-nonce" },
      }),
    );
    // Real reply from Alice.
    await c.__testFeedFrame(
      "target-1",
      makeMsg({
        id: "r1",
        room_id: "target-1",
        participant_id: "agent-a",
        content: "here you go",
        metadata: { _nonce: "n1" },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));

    const completed = sent.find(
      (s) =>
        s.roomId === "source-1" &&
        (s.metadata?.room_query_result as { status?: string })?.status === "completed",
    );
    expect(completed).toBeDefined();
    expect(completed!.content).toContain("here you go");
    // And importantly the ghost [ROOM_QUERY] did not count.
    expect(completed!.content).not.toContain("echoing myself");
  });
});
