// Room query coordinator — representative-agent side of the
// cross-room mention flow.
//
// Ported from
// ``packages/agent/doorae_agent/integrations/room_query.py``.
//
// When the server decorates a message with ``room_query`` metadata,
// the representative agent of the source room forwards the question
// to the target room, collects responses, and delivers a synthesised
// summary back. Solo / completed / timeout branches all produce the
// same on-the-wire shape — only the ``status`` tag differs.

import type { ChatClient, MessageHandler } from "../client.js";
import type { MessageOut } from "../protocol/frames.js";
import { log } from "../logging.js";

export const COLLECT_TIMEOUT_MS = 5 * 60 * 1000;
const TARGET_JOIN_SETTLE_MS = 1_000;

// Strip ``<#room:<id>>`` tokens from the forwarded content so the
// server's ``parse_mentions`` on the target side doesn't re-attach
// ``room_query`` metadata and kick off an infinite forwarding loop.
const ROOM_MENTION_TOKEN_RE = /<#room:[^>]+>\s*/g;

export interface RoomQuery {
  targetRoomId: string;
  sourceRoomId: string;
  content: string;
  /** Empty for legacy in-flight messages from before #55. */
  queryId: string;
  /** Original human author's participant id (#55 forward badge). */
  sourceParticipantId: string | null;
}

export interface Participant {
  id?: string;
  kind?: string;
  online?: boolean;
  display_name?: string;
  last_seen_at?: string;
}

export function parseRoomQuery(msg: MessageOut): RoomQuery | null {
  const metadata = (msg.metadata ?? {}) as Record<string, unknown>;
  const rq = metadata["room_query"] as Record<string, unknown> | undefined;
  if (!rq) return null;
  return {
    targetRoomId: String(rq["target_room_id"] ?? ""),
    sourceRoomId: String(rq["source_room_id"] ?? ""),
    content: msg.content ?? "",
    queryId: (rq["query_id"] as string | undefined) ?? "",
    sourceParticipantId:
      (rq["source_participant_id"] as string | null | undefined) ?? null,
  };
}

export function stripRoomMention(content: string): string {
  const cleaned = content.replace(ROOM_MENTION_TOKEN_RE, "");
  return cleaned.replace(/[ \t]{2,}/g, " ").trim();
}

/**
 * Format a relative "N분 전" timestamp from an ISO 8601 string. Mirrors
 * ``_fmt_ago`` in the Python helper. Unparseable/missing values map to
 * ``"알 수 없음"`` so log/summary lines never crash.
 */
export function fmtAgo(
  lastSeenAt: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!lastSeenAt) return "알 수 없음";
  const ts = new Date(lastSeenAt);
  if (Number.isNaN(ts.getTime())) return "알 수 없음";
  const seconds = Math.floor((now.getTime() - ts.getTime()) / 1000);
  if (seconds < 60) return "방금 전";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86_400)}일 전`;
}

/**
 * Execute a room query. Non-blocking: joins the target room (if not
 * already), sends the forward, and registers a multi-reply callback
 * that fires on completion or the 5-minute timeout.
 */
export async function executeRoomQuery(
  client: ChatClient,
  _msg: MessageOut,
  query: RoomQuery,
  opts: { timeoutMs?: number; joinSettleMs?: number } = {},
): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? COLLECT_TIMEOUT_MS;
  const joinSettleMs = opts.joinSettleMs ?? TARGET_JOIN_SETTLE_MS;

  if (!client.isConnected(query.targetRoomId)) {
    await client.joinRoom(query.targetRoomId);
    if (joinSettleMs > 0) {
      await new Promise((r) => setTimeout(r, joinSettleMs));
    }
  }

  const participants = (await client.getRoomParticipants(query.targetRoomId)) as Participant[];
  const myPids = client.myParticipantIds;
  const agentCandidates = participants.filter(
    (p) => p.kind === "agent" && !(p.id && myPids.has(p.id)),
  );
  // #54 — offline agents count toward ``candidates`` but not
  // ``expected_count`` so a dead process doesn't time out the whole
  // query.
  const onlineAgents = agentCandidates.filter((p) => p.online !== false);
  const expectedCount = onlineAgents.length;

  if (expectedCount === 0) {
    log.info({ target: query.targetRoomId }, "room_query.solo");
    await deliverResult(client, {
      sourceRoomId: query.sourceRoomId,
      targetRoomId: query.targetRoomId,
      queryId: query.queryId,
      question: query.content,
      responses: [],
      expectedCount: 0,
      status: "solo",
      candidates: agentCandidates,
    });
    return;
  }

  const forwardedBody = stripRoomMention(query.content) || query.content;
  await client.send(
    query.targetRoomId,
    `[ROOM_QUERY] ${forwardedBody}`,
    {
      room_query_forward: {
        source_room_id: query.sourceRoomId,
        source_participant_id: query.sourceParticipantId,
        query_id: query.queryId,
      },
    },
  );

  registerMultiReplyCallback(client, {
    sourceRoomId: query.sourceRoomId,
    targetRoomId: query.targetRoomId,
    queryId: query.queryId,
    expectedCount,
    question: query.content,
    candidates: agentCandidates,
    timeoutMs,
  });
}

interface DeliverArgs {
  sourceRoomId: string;
  targetRoomId: string;
  queryId: string;
  question: string;
  responses: Array<{ participant_id: string; content: string }>;
  expectedCount: number;
  status: "solo" | "completed" | "timeout";
  candidates: Participant[];
}

async function deliverResult(client: ChatClient, args: DeliverArgs): Promise<void> {
  const total = args.responses.length;
  const missing = Math.max(args.expectedCount - total, 0);

  let body: string;
  if (args.status === "solo") {
    const header = "[취합 결과] (대상 방에 응답할 에이전트가 없음)";
    body = `${header}\n\n질문: ${args.question}`;
  } else {
    let header = `[취합 결과] (${total}/${args.expectedCount}명 응답)`;
    if (missing > 0) header += ` — ${missing}명 미응답`;
    const parts = args.responses.map((r, i) => `응답 ${i + 1}: ${r.content}`);
    const summary = parts.join("\n");
    body = `${header}\n\n질문: ${args.question}\n\n${summary}`;

    // #54 — annotate which candidates missed.
    const respondedPids = new Set(args.responses.map((r) => r.participant_id));
    const missingLines: string[] = [];
    for (const p of args.candidates) {
      if (p.id && respondedPids.has(p.id)) continue;
      const name = p.display_name ?? p.id ?? "unknown";
      if (p.online === false) {
        const ago = fmtAgo(p.last_seen_at ?? null);
        missingLines.push(`- ${name} (offline, 마지막 응답 ${ago})`);
      } else {
        missingLines.push(`- ${name} (응답 없음)`);
      }
    }
    if (missingLines.length > 0) {
      body += `\n\n미응답:\n${missingLines.join("\n")}`;
    }
  }

  await client.send(args.sourceRoomId, body, {
    room_query_result: {
      query_id: args.queryId,
      target_room_id: args.targetRoomId,
      responded: total,
      expected: args.expectedCount,
      status: args.status,
      responses: args.responses.map((r) => ({
        participant_id: r.participant_id,
        content: r.content,
      })),
    },
  });
}

interface CallbackCtx {
  sourceRoomId: string;
  targetRoomId: string;
  queryId: string;
  expectedCount: number;
  question: string;
  candidates: Participant[];
  timeoutMs: number;
}

function registerMultiReplyCallback(client: ChatClient, ctx: CallbackCtx): void {
  const myPids = client.myParticipantIds;
  const responses: Array<{ participant_id: string; content: string }> = [];
  let done = false;

  const handler: MessageHandler = async (m: MessageOut) => {
    if (done) return;
    if (m.room_id !== ctx.targetRoomId) return;
    const sender = m.participant_id;
    if (sender && myPids.has(sender)) return;
    const content = m.content ?? "";
    // Skip our own [ROOM_QUERY] forward (in case the hard/nonce
    // filters missed it — e.g. a ghost participant id after reconnect).
    if (content.startsWith("[ROOM_QUERY]")) return;

    responses.push({
      participant_id: sender ?? "unknown",
      content,
    });
    log.info(
      { count: responses.length, expected: ctx.expectedCount },
      "room_query.response_collected",
    );

    if (responses.length >= ctx.expectedCount) {
      done = true;
      await deliverResult(client, {
        sourceRoomId: ctx.sourceRoomId,
        targetRoomId: ctx.targetRoomId,
        queryId: ctx.queryId,
        question: ctx.question,
        responses,
        expectedCount: ctx.expectedCount,
        status: "completed",
        candidates: ctx.candidates,
      });
      client.offMessage(handler);
    }
  };

  client.onMessage(handler);

  // Safety timeout.
  setTimeout(async () => {
    if (done) return;
    done = true;
    log.warn(
      { collected: responses.length, expected: ctx.expectedCount },
      "room_query.timeout",
    );
    try {
      await deliverResult(client, {
        sourceRoomId: ctx.sourceRoomId,
        targetRoomId: ctx.targetRoomId,
        queryId: ctx.queryId,
        question: ctx.question,
        responses,
        expectedCount: ctx.expectedCount,
        status: "timeout",
        candidates: ctx.candidates,
      });
    } catch (exc) {
      log.error({ error: String(exc) }, "room_query.timeout_deliver_error");
    }
    client.offMessage(handler);
  }, ctx.timeoutMs);
}
