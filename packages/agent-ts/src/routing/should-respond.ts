// Unified response gate for incoming messages.
//
// Ported from ``packages/agent/doorae_agent/integrations/base.py``:
// ``should_respond``. The rules are unchanged — every comment on the
// Python side applies here too. Keep the two in lock-step: the Python
// version has the canonical test suite (``test_should_respond.py``)
// that we replay here.
//
// Rules (evaluated in order):
//   1. Own message → false
//   2. ``[DELEGATED]`` / ``[ROOM_QUERY]`` prefix or ``room_query``
//      metadata → true (with #61 gate for the metadata path).
//   3. Server-parsed explicit mention matching this agent → true.
//   4. Explicit mentions exist but NOT for us → false.
//   5. No addressable mentions + human sender → true.
//   6. Agent sender, no mention → false.

import type { MessageOut } from "../protocol/frames.js";

/**
 * Minimal surface needed from the ChatClient. Keeps this rule engine
 * free of WS/HTTP concerns so tests can drive it with plain objects.
 */
export interface RoutingContext {
  agentName: string;
  myParticipantIds: Set<string>;
  /** Agent identity from the welcome frame, null on user/guest conns. */
  agentId: string | null;
}

interface UserMention {
  type: "user";
  id: string;
}

interface LegacyMention {
  type: "legacy";
  name: string;
}

interface RoomMention {
  type: "room";
  id?: string;
}

type Mention = UserMention | LegacyMention | RoomMention | Record<string, unknown>;

function isAddressable(m: Mention): m is UserMention | LegacyMention {
  const t = (m as { type?: unknown }).type;
  return t === "user" || t === "legacy";
}

/**
 * Escape a string for use as a literal in a regex. Kept tiny on
 * purpose — matches the Python ``re.escape`` surface we use.
 */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function shouldRespond(msg: MessageOut, ctx: RoutingContext): boolean {
  const content = msg.content ?? "";
  const sender = msg.participant_id;
  const metadata = (msg.metadata ?? {}) as Record<string, unknown>;

  // 1. Self-message.
  if (sender && ctx.myParticipantIds.has(sender)) {
    return false;
  }

  // 2a. Task-init prefix → always respond.
  if (content.startsWith("[DELEGATED]") || content.startsWith("[ROOM_QUERY]")) {
    return true;
  }

  // 2b. room_query metadata — #61 representative gate.
  const roomQuery = metadata["room_query"] as Record<string, unknown> | undefined;
  if (roomQuery) {
    const repId = roomQuery["representative_agent_id"];
    if (typeof repId === "string" && ctx.agentId) {
      return ctx.agentId === repId;
    }
    // Legacy/transition path: pre-#61 servers or clients without an
    // agent_id fall back to the old "always forward" behaviour so the
    // deploy transition doesn't drop queries entirely.
    return true;
  }

  const rawMentions = (metadata["mentions"] ?? []) as Mention[];
  // Only user/legacy mentions route to a specific participant. Room
  // mentions drive cross-room queries and are handled above; they
  // must not silence this agent.
  const addressable = Array.isArray(rawMentions)
    ? rawMentions.filter((m): m is UserMention | LegacyMention =>
        typeof m === "object" && m !== null && isAddressable(m as Mention),
      )
    : [];

  const agentKey = ctx.agentName ? ctx.agentName.toLowerCase() : null;

  const targetsMe = (m: UserMention | LegacyMention): boolean => {
    if (m.type === "legacy") {
      return (
        !!agentKey &&
        typeof m.name === "string" &&
        m.name.toLowerCase() === agentKey
      );
    }
    // m.type === "user" — id is a participant_id; compare directly
    // against ``myParticipantIds``. This guards against the "every
    // agent replies to @guest" fan-out bug.
    return typeof m.id === "string" && ctx.myParticipantIds.has(m.id);
  };

  let mentionedMe = addressable.some(targetsMe);

  // Backward-compat content scan for names the server's legacy
  // ``@([\w-]+)`` regex can't capture (e.g. "@테스트 에이전트"). The
  // ``(?![\w:])`` lookahead is load-bearing — without it an agent
  // literally named ``user`` would match the ``<@user:<pid>>`` token
  // and re-open the fan-out bug.
  if (!mentionedMe && ctx.agentName) {
    const pattern = new RegExp(`@${escapeRegex(ctx.agentName)}(?![\\w:])`, "i");
    if (pattern.test(content)) mentionedMe = true;
  }

  // 3. Directly mentioned → respond.
  if (mentionedMe) return true;

  // 4. Mentions present but not for us → stay out.
  if (addressable.length > 0) return false;

  // 5. No addressable mentions. Humans talking generally → respond.
  const senderIsAgent = typeof metadata["_nonce"] === "string";
  if (!senderIsAgent) return true;

  // 6. Agent sender, no mention → skip.
  return false;
}
