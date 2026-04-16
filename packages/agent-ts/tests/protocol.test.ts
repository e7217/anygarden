import { describe, it, expect } from "vitest";
import {
  parseOutgoingFrame,
  safeParseOutgoingFrame,
  SendFrameSchema,
  TypingFrameSchema,
  JoinRoomFrameSchema,
  CreateRoomFrameSchema,
  MessageOutSchema,
  WelcomeOutSchema,
  TypingOutSchema,
  ErrorOutSchema,
  PresenceUpdateOutSchema,
  type OutgoingFrame,
} from "../src/protocol/frames.js";
import { buildSubprotocols, SUBPROTOCOL, PROTOCOL_VERSION, isCompatible } from "../src/protocol/auth.js";

describe("protocol/auth", () => {
  it("builds subprotocol list with bearer token", () => {
    expect(buildSubprotocols("abc.def")).toEqual([SUBPROTOCOL, "bearer.abc.def"]);
  });

  it("accepts matching server protocol version", () => {
    expect(isCompatible(PROTOCOL_VERSION)).toBe(true);
    expect(isCompatible("v2")).toBe(false);
  });
});

describe("protocol/frames — incoming (agent → server)", () => {
  it("parses a SendFrame with metadata", () => {
    const parsed = SendFrameSchema.parse({
      type: "send",
      content: "hello",
      metadata: { _nonce: "abc" },
    });
    expect(parsed.content).toBe("hello");
    expect(parsed.metadata).toEqual({ _nonce: "abc" });
  });

  it("parses a SendFrame without metadata", () => {
    const parsed = SendFrameSchema.parse({ type: "send", content: "hi" });
    expect(parsed.metadata).toBeUndefined();
  });

  it("parses a TypingFrame with default is_typing", () => {
    const parsed = TypingFrameSchema.parse({ type: "typing" });
    expect(parsed.is_typing).toBe(true);
  });

  it("parses a JoinRoomFrame", () => {
    const parsed = JoinRoomFrameSchema.parse({ type: "join_room", room_id: "r1" });
    expect(parsed.room_id).toBe("r1");
  });

  it("parses a CreateRoomFrame", () => {
    const parsed = CreateRoomFrameSchema.parse({
      type: "create_room",
      project_id: "p1",
      name: "channel",
    });
    expect(parsed.is_dm).toBe(false);
  });
});

describe("protocol/frames — outgoing (server → agent)", () => {
  it("parses a MessageOut", () => {
    const frame: OutgoingFrame = parseOutgoingFrame({
      type: "message",
      id: "m1",
      room_id: "r1",
      participant_id: "p1",
      content: "hi",
      seq: 5,
      created_at: "2026-04-16T00:00:00Z",
      metadata: { _nonce: "abc" },
    });
    expect(frame.type).toBe("message");
    if (frame.type === "message") {
      expect(frame.seq).toBe(5);
      expect(frame.participant_id).toBe("p1");
    }
  });

  it("parses a MessageOut with null participant_id (removed sender)", () => {
    // The Python ``MessageOut.participant_id`` is ``Optional[str]`` —
    // SET NULL foreign keys produce a null value when the original
    // sender was removed from the room.
    const frame = MessageOutSchema.parse({
      type: "message",
      room_id: "r1",
      participant_id: null,
      content: "hi",
      seq: 1,
      created_at: "2026-04-16T00:00:00Z",
    });
    expect(frame.participant_id).toBeNull();
  });

  it("parses a WelcomeOut with agent_id and pending_rooms", () => {
    const frame = WelcomeOutSchema.parse({
      type: "welcome",
      participant_id: "p1",
      pending_rooms: ["r2", "r3"],
      agent_id: "agent-rep",
    });
    expect(frame.agent_id).toBe("agent-rep");
    expect(frame.pending_rooms).toEqual(["r2", "r3"]);
  });

  it("parses a WelcomeOut without agent_id (user/guest conn)", () => {
    const frame = WelcomeOutSchema.parse({
      type: "welcome",
      participant_id: "p1",
    });
    expect(frame.agent_id).toBeFalsy();
    expect(frame.pending_rooms).toEqual([]);
  });

  it("parses a TypingOut from another participant", () => {
    const frame = TypingOutSchema.parse({
      type: "typing",
      room_id: "r1",
      participant_id: "p2",
      is_typing: true,
    });
    expect(frame.is_typing).toBe(true);
  });

  it("parses a PresenceUpdateOut with last_seen_at", () => {
    const frame = PresenceUpdateOutSchema.parse({
      type: "presence_update",
      room_id: "r1",
      participant_id: "p1",
      online: false,
      last_seen_at: "2026-04-16T00:00:00Z",
    });
    expect(frame.online).toBe(false);
  });

  it("parses an ErrorOut", () => {
    const frame = ErrorOutSchema.parse({ type: "error", detail: "forbidden" });
    expect(frame.detail).toBe("forbidden");
  });

  it("discriminated union dispatches by `type`", () => {
    const welcome = parseOutgoingFrame({
      type: "welcome",
      participant_id: "p1",
    });
    const join = parseOutgoingFrame({
      type: "join_room",
      room_id: "r1",
      participant_id: "p1",
    });
    expect(welcome.type).toBe("welcome");
    expect(join.type).toBe("join_room");
  });

  it("safeParse returns null for unknown frame type", () => {
    expect(safeParseOutgoingFrame({ type: "never_defined" })).toBeNull();
  });

  it("safeParse returns null for missing required fields", () => {
    expect(safeParseOutgoingFrame({ type: "message" })).toBeNull();
  });

  it("throws on unknown frame type for strict parse", () => {
    expect(() => parseOutgoingFrame({ type: "mystery" })).toThrow();
  });
});
