// Ported from
// ``packages/agent/tests/test_integrations/test_should_respond.py`` —
// every test case there has a counterpart here. When changing the
// routing rules, update both files in lock-step.

import { describe, it, expect } from "vitest";
import { shouldRespond, type RoutingContext } from "../src/routing/should-respond.js";
import type { MessageOut } from "../src/protocol/frames.js";

function makeCtx(
  overrides: Partial<RoutingContext> = {},
): RoutingContext {
  return {
    agentName: "테스트에이전트",
    myParticipantIds: new Set(["my-pid-123"]),
    agentId: null,
    ...overrides,
  };
}

function makeMsg(overrides: Partial<MessageOut> = {}): MessageOut {
  return {
    type: "message",
    id: "m",
    room_id: "r",
    participant_id: "other",
    content: "hi",
    seq: 1,
    created_at: "2026-04-16T00:00:00Z",
    metadata: null,
    ...overrides,
  };
}

describe("shouldRespond — parity with Python test_should_respond.py", () => {
  it("skips own message", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "my-pid-123",
      content: "hello",
      metadata: {},
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("always responds to [DELEGATED]", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other",
      content: "[DELEGATED] do something",
      metadata: { _nonce: "x" },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("responds when mentioned via user-id token targeting me", () => {
    const ctx = makeCtx({ myParticipantIds: new Set(["alice-pid"]) });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<@user:alice-pid> 이거 봐줘",
      metadata: { mentions: [{ type: "user", id: "alice-pid" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("skips user-id token targeting a different participant", () => {
    const ctx = makeCtx({ myParticipantIds: new Set(["alice-pid"]) });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<@user:bob-pid> 이거 봐줘",
      metadata: { mentions: [{ type: "user", id: "bob-pid" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("guest user-id mention silences all agents (#regression)", () => {
    const alice: RoutingContext = makeCtx({
      agentName: "Alice",
      myParticipantIds: new Set(["alice-pid"]),
    });
    const bob: RoutingContext = makeCtx({
      agentName: "Bob",
      myParticipantIds: new Set(["bob-pid"]),
    });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<@user:guest-pid> 안녕하세요",
      metadata: { mentions: [{ type: "user", id: "guest-pid" }] },
    });
    expect(shouldRespond(msg, alice)).toBe(false);
    expect(shouldRespond(msg, bob)).toBe(false);
  });

  it("responds when mentioned via legacy token with matching name", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other",
      content: "@테스트에이전트 안녕",
      metadata: {
        mentions: [{ type: "legacy", name: "테스트에이전트" }],
        _nonce: "x",
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("content scan falls back for names with whitespace", () => {
    const ctx = makeCtx({ agentName: "테스트 에이전트" });
    const msg = makeMsg({
      participant_id: "other",
      content: "@테스트 에이전트 안녕",
      metadata: { _nonce: "x" },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("human message without mention still responds (default)", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "안녕하세요",
      metadata: {},
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("human message addressed to another agent skips (multi-agent fan-out fix)", () => {
    const ctx = makeCtx({ agentName: "앨리스" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "@밥 이거 봐줘",
      metadata: { mentions: [{ type: "legacy", name: "밥" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("multi-mention including me responds", () => {
    const ctx = makeCtx({ agentName: "앨리스" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "@앨리스 @밥 회의 내용 정리",
      metadata: {
        mentions: [
          { type: "legacy", name: "앨리스" },
          { type: "legacy", name: "밥" },
        ],
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("room-only mention does not suppress response", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<#room:xyz> 의견 좀",
      metadata: { mentions: [{ type: "room", id: "xyz" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("agent message without mention skips", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other-agent",
      content: "네, 알겠습니다.",
      metadata: { _nonce: "some-nonce" },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("agent message mentioning me responds", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other-agent",
      content: "@테스트에이전트 이거 봐줘",
      metadata: {
        mentions: [{ type: "legacy", name: "테스트에이전트" }],
        _nonce: "x",
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("content scan does NOT match the <@user:pid> id token", () => {
    // Agent literally named "user" — the content scan must NOT match
    // the substring ``@user`` inside the ``<@user:<pid>>`` token.
    const ctx = makeCtx({
      agentName: "user",
      myParticipantIds: new Set(["unrelated-pid"]),
    });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<@user:bob-pid> 확인해줘",
      metadata: { mentions: [{ type: "user", id: "bob-pid" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("content scan matches a name with whitespace", () => {
    const ctx = makeCtx({ agentName: "Alice Kim" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "@Alice Kim 봐줄래?",
      metadata: { mentions: [{ type: "legacy", name: "Bob" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("mention matching is case-insensitive", () => {
    const ctx = makeCtx({ agentName: "Alice" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "@alice 이거 봐줘",
      metadata: { mentions: [{ type: "legacy", name: "alice" }] },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("agent-to-agent chatter for someone else does not drag me in", () => {
    const ctx = makeCtx({ agentName: "앨리스" });
    const msg = makeMsg({
      participant_id: "other-agent",
      content: "@밥 확인해줘",
      metadata: {
        mentions: [{ type: "legacy", name: "밥" }],
        _nonce: "x",
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("no metadata at all — human → respond", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "human",
      content: "hello",
      metadata: null,
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("room_query metadata (legacy, no rep id) responds", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other-agent",
      content: "<#room:xyz> 의견?",
      metadata: {
        _nonce: "x",
        room_query: { target_room_id: "xyz", source_room_id: "abc" },
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("room_query with rep id matching agent_id responds (#61)", () => {
    const ctx = makeCtx({ agentId: "agent-rep-123" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<#room:xyz> 의견?",
      metadata: {
        room_query: {
          target_room_id: "xyz",
          source_room_id: "abc",
          representative_agent_id: "agent-rep-123",
        },
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("room_query with rep id NOT matching agent_id skips (#61)", () => {
    const ctx = makeCtx({ agentId: "agent-other-456" });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<#room:xyz> 의견?",
      metadata: {
        room_query: {
          target_room_id: "xyz",
          source_room_id: "abc",
          representative_agent_id: "agent-rep-123",
        },
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(false);
  });

  it("legacy pre-#61 client with no agent_id responds (transition fallback)", () => {
    const ctx = makeCtx({ agentId: null });
    const msg = makeMsg({
      participant_id: "human-pid",
      content: "<#room:xyz> 의견?",
      metadata: {
        room_query: {
          target_room_id: "xyz",
          source_room_id: "abc",
          representative_agent_id: "agent-rep-123",
        },
      },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });

  it("[ROOM_QUERY] prefix always responds", () => {
    const ctx = makeCtx();
    const msg = makeMsg({
      participant_id: "other-agent",
      content: "[ROOM_QUERY] 디자인룸에서 질문: API 설계 의견?",
      metadata: { _nonce: "x" },
    });
    expect(shouldRespond(msg, ctx)).toBe(true);
  });
});
