// Zod schemas mirroring ``packages/cluster/anygarden/ws/protocol.py``.
//
// These are direct 1:1 ports of the Pydantic v2 models the server uses.
// Any change on the Python side must be reflected here; the TS runtime
// parses incoming frames via ``parseOutgoingFrame`` at the receive
// boundary so drift surfaces as a parse error rather than a silent
// miscategorised message.
//
// Reference: ``packages/cluster/anygarden/ws/protocol.py``
//
// Naming: the Python module calls client→server frames "IncomingFrame"
// (from the server's perspective) and server→client frames "OutgoingFrame".
// We keep the same naming so cross-package greps line up.

import { z } from "zod";

// ── Incoming (agent → server) ─────────────────────────────────────────

export const SendFrameSchema = z.object({
  type: z.literal("send"),
  content: z.string(),
  metadata: z.record(z.string(), z.unknown()).nullish(),
});
export type SendFrame = z.infer<typeof SendFrameSchema>;

export const TypingFrameSchema = z.object({
  type: z.literal("typing"),
  is_typing: z.boolean().default(true),
});
export type TypingFrame = z.infer<typeof TypingFrameSchema>;

export const CreateRoomFrameSchema = z.object({
  type: z.literal("create_room"),
  project_id: z.string(),
  name: z.string(),
  is_dm: z.boolean().default(false),
});
export type CreateRoomFrame = z.infer<typeof CreateRoomFrameSchema>;

export const JoinRoomFrameSchema = z.object({
  type: z.literal("join_room"),
  room_id: z.string(),
});
export type JoinRoomFrame = z.infer<typeof JoinRoomFrameSchema>;

export const IncomingFrameSchema = z.discriminatedUnion("type", [
  SendFrameSchema,
  TypingFrameSchema,
  CreateRoomFrameSchema,
  JoinRoomFrameSchema,
]);
export type IncomingFrame = z.infer<typeof IncomingFrameSchema>;

// ── Outgoing (server → agent) ─────────────────────────────────────────

export const MessageOutSchema = z.object({
  type: z.literal("message"),
  id: z.string().default(""),
  room_id: z.string(),
  participant_id: z.string().nullable(),
  content: z.string(),
  seq: z.number(),
  // server emits ISO-8601 datetime; we keep it as a string so we don't
  // need a date adapter in hot paths.
  created_at: z.string(),
  metadata: z.record(z.string(), z.unknown()).nullish(),
});
export type MessageOut = z.infer<typeof MessageOutSchema>;

export const RoomCreatedOutSchema = z.object({
  type: z.literal("room_created"),
  room_id: z.string(),
  name: z.string(),
});
export type RoomCreatedOut = z.infer<typeof RoomCreatedOutSchema>;

export const JoinRoomOutSchema = z.object({
  type: z.literal("join_room"),
  room_id: z.string(),
  participant_id: z.string(),
});
export type JoinRoomOut = z.infer<typeof JoinRoomOutSchema>;

export const RoomDeletedOutSchema = z.object({
  type: z.literal("room_deleted"),
  room_id: z.string(),
});
export type RoomDeletedOut = z.infer<typeof RoomDeletedOutSchema>;

export const RoomMembershipChangedOutSchema = z.object({
  type: z.literal("room_membership_changed"),
  action: z.enum(["added", "removed"]),
  room_id: z.string(),
  user_id: z.string(),
});
export type RoomMembershipChangedOut = z.infer<typeof RoomMembershipChangedOutSchema>;

export const RoomPinOrderChangedOutSchema = z.object({
  type: z.literal("room_pin_order_changed"),
  user_id: z.string(),
  pinned_room_ids: z.array(z.string()),
});
export type RoomPinOrderChangedOut = z.infer<typeof RoomPinOrderChangedOutSchema>;

export const TypingOutSchema = z.object({
  type: z.literal("typing"),
  room_id: z.string(),
  participant_id: z.string(),
  is_typing: z.boolean(),
});
export type TypingOut = z.infer<typeof TypingOutSchema>;

export const PresenceUpdateOutSchema = z.object({
  type: z.literal("presence_update"),
  room_id: z.string(),
  participant_id: z.string(),
  online: z.boolean(),
  last_seen_at: z.string().nullish(),
});
export type PresenceUpdateOut = z.infer<typeof PresenceUpdateOutSchema>;

export const WelcomeOutSchema = z.object({
  type: z.literal("welcome"),
  participant_id: z.string(),
  pending_rooms: z.array(z.string()).default([]),
  // Issue #61 — present only on agent connections. Used to gate
  // room_query forwarding.
  agent_id: z.string().nullish(),
});
export type WelcomeOut = z.infer<typeof WelcomeOutSchema>;

export const ErrorOutSchema = z.object({
  type: z.literal("error"),
  detail: z.string(),
});
export type ErrorOut = z.infer<typeof ErrorOutSchema>;

export const OutgoingFrameSchema = z.discriminatedUnion("type", [
  MessageOutSchema,
  RoomCreatedOutSchema,
  JoinRoomOutSchema,
  RoomDeletedOutSchema,
  RoomMembershipChangedOutSchema,
  RoomPinOrderChangedOutSchema,
  TypingOutSchema,
  PresenceUpdateOutSchema,
  WelcomeOutSchema,
  ErrorOutSchema,
]);
export type OutgoingFrame = z.infer<typeof OutgoingFrameSchema>;

/**
 * Parse a JSON-decoded frame from the server. Throws a ZodError when
 * the frame doesn't match any known shape — callers should log the
 * frame and continue (mirrors the Python client which logs bad_frame
 * and skips).
 */
export function parseOutgoingFrame(data: unknown): OutgoingFrame {
  return OutgoingFrameSchema.parse(data);
}

/**
 * Try to parse, returning ``null`` on failure. Preferred over throwing
 * inside the WS read loop so one malformed frame doesn't kill the
 * whole room connection.
 */
export function safeParseOutgoingFrame(data: unknown): OutgoingFrame | null {
  const result = OutgoingFrameSchema.safeParse(data);
  return result.success ? result.data : null;
}
