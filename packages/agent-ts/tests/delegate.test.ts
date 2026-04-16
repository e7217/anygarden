import { describe, it, expect, vi } from "vitest";
import { ChatClient } from "../src/client.js";
import { parseDelegate, executeDelegate } from "../src/coordination/delegate.js";
import type { MessageOut } from "../src/protocol/frames.js";

describe("parseDelegate", () => {
  it("parses a bare /delegate command", () => {
    const r = parseDelegate("/delegate dev write the README");
    expect(r).toEqual({ subRoomName: "dev", task: "write the README" });
  });

  it("parses after an @mention prefix with spaces in the name", () => {
    const r = parseDelegate("@테스트 에이전트 /delegate dev implement foo");
    expect(r).toEqual({ subRoomName: "dev", task: "implement foo" });
  });

  it("returns null when content is not a delegate command", () => {
    expect(parseDelegate("hello there")).toBeNull();
    expect(parseDelegate("/delegat dev foo")).toBeNull();
  });

  it("handles multi-line tasks", () => {
    const r = parseDelegate("/delegate dev line 1\nline 2");
    expect(r?.task).toBe("line 1\nline 2");
  });
});

function makeMsg(overrides: Partial<MessageOut> = {}): MessageOut {
  return {
    type: "message",
    id: "m1",
    room_id: "parent",
    participant_id: "human-pid",
    content: "/delegate dev please implement",
    seq: 1,
    created_at: "2026-04-16T00:00:00Z",
    metadata: null,
    ...overrides,
  };
}

describe("executeDelegate", () => {
  it("reports failure when sub-room cannot be resolved", async () => {
    const sent: Array<{ roomId: string; content: string }> = [];
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "findSubRoom").mockResolvedValue(null);
    vi.spyOn(c, "send").mockImplementation(async (roomId, content) => {
      sent.push({ roomId, content });
    });
    vi.spyOn(c, "joinRoom").mockResolvedValue();

    const msg = makeMsg();
    await executeDelegate(c, msg, { subRoomName: "missing", task: "t" });
    expect(sent).toEqual([
      { roomId: "parent", content: "서브룸 'missing' 를 찾을 수 없습니다" },
    ]);
  });

  it("sends confirmation to parent and [DELEGATED] to sub-room", async () => {
    const sent: Array<{ roomId: string; content: string }> = [];
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "findSubRoom").mockResolvedValue("sub-1");
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    vi.spyOn(c, "send").mockImplementation(async (roomId, content) => {
      sent.push({ roomId, content });
    });

    await executeDelegate(c, makeMsg(), { subRoomName: "dev", task: "make it so" });
    expect(sent).toEqual([
      { roomId: "parent", content: "서브룸 'dev' 에 작업을 전달했습니다" },
      { roomId: "sub-1", content: "[DELEGATED] make it so" },
    ]);
  });

  it("posts the first sub-room reply back to the parent room", async () => {
    const sent: Array<{ roomId: string; content: string }> = [];
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    vi.spyOn(c, "findSubRoom").mockResolvedValue("sub-1");
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    const sendSpy = vi.spyOn(c, "send").mockImplementation(async (roomId, content) => {
      sent.push({ roomId, content });
    });

    await executeDelegate(c, makeMsg(), { subRoomName: "dev", task: "t" });
    expect(sendSpy).toHaveBeenCalledTimes(2);

    // Simulate a reply from another agent in the sub-room.
    await c.__testFeedFrame(
      "sub-1",
      makeMsg({
        id: "r1",
        room_id: "sub-1",
        participant_id: "other-agent",
        content: "here's the answer",
        metadata: { _nonce: "x" },
      }),
    );

    // Let the handler run.
    await new Promise((resolve) => setTimeout(resolve, 0));

    // 3rd send call should be the reply forwarded to the parent room.
    const last = sent[sent.length - 1];
    expect(last.roomId).toBe("parent");
    expect(last.content).toContain("서브룸 'dev' 결과:");
    expect(last.content).toContain("here's the answer");
  });

  it("ignores sub-room messages from our own participant", async () => {
    const sent: Array<{ roomId: string; content: string }> = [];
    const c = new ChatClient({ serverUrl: "ws://test.local", token: "t" });
    c.myParticipantIds.add("my-sub-pid");
    vi.spyOn(c, "findSubRoom").mockResolvedValue("sub-1");
    vi.spyOn(c, "isConnected").mockReturnValue(true);
    vi.spyOn(c, "send").mockImplementation(async (roomId, content) => {
      sent.push({ roomId, content });
    });

    await executeDelegate(c, makeMsg(), { subRoomName: "dev", task: "t" });
    // Feed a self-message — hard filter drops before handler fires,
    // so the delegate reply callback does NOT pick it up.
    await c.__testFeedFrame(
      "sub-1",
      makeMsg({
        id: "r1",
        room_id: "sub-1",
        participant_id: "my-sub-pid",
        content: "my own echo",
        metadata: { _nonce: "x" },
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Still only the 2 confirmation + delegate sends.
    expect(sent.length).toBe(2);
  });
});
