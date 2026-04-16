// Delegate command — forward tasks from the parent room to a sub-room.
//
// Ported from ``packages/agent/doorae_agent/integrations/delegate.py``.
// V1 semantics: the first reply from the sub-room agent is posted back
// to the parent room as the task result.

import type { ChatClient, MessageHandler } from "../client.js";
import type { MessageOut } from "../protocol/frames.js";
import { log } from "../logging.js";

const REPLY_TIMEOUT_MS = 5 * 60 * 1000;
const SUBROOM_JOIN_SETTLE_MS = 1_000;

const DELEGATE_RE = /^\/delegate\s+(\S+)\s+([\s\S]+)/;

export interface DelegateRequest {
  subRoomName: string;
  task: string;
}

/**
 * Parse ``/delegate <sub-room-name> <task>`` from message content.
 * Returns ``null`` when the content doesn't match. The command can be
 * embedded after an ``@mention`` prefix so ``@agent /delegate dev
 * implement foo`` works too — we search for ``/delegate `` and match
 * from there.
 */
export function parseDelegate(content: string): DelegateRequest | null {
  const idx = content.indexOf("/delegate ");
  if (idx < 0) return null;
  const after = content.slice(idx);
  const m = DELEGATE_RE.exec(after);
  if (!m) return null;
  return { subRoomName: m[1], task: m[2].trim() };
}

/**
 * Execute the delegate workflow. Non-blocking — fires the sub-room
 * task and registers a one-shot reply callback that posts the
 * result back to the parent room.
 *
 * Flow:
 *   1. Resolve the sub-room id via REST.
 *   2. Ensure we're connected to the sub-room.
 *   3. Post confirmation to the parent room.
 *   4. Send ``[DELEGATED] <task>`` to the sub-room.
 *   5. Register a reply callback (auto-cleaned on fire or timeout).
 */
export async function executeDelegate(
  client: ChatClient,
  msg: MessageOut,
  req: DelegateRequest,
): Promise<void> {
  const parentRoomId = msg.room_id;

  const subRoomId = await client.findSubRoom(parentRoomId, req.subRoomName);
  if (!subRoomId) {
    await client.send(
      parentRoomId,
      `서브룸 '${req.subRoomName}' 를 찾을 수 없습니다`,
    );
    return;
  }

  if (!client.isConnected(subRoomId)) {
    await client.joinRoom(subRoomId);
    // Brief settle so the server sends us the welcome frame before we
    // start pushing. Mirrors the Python sleep(1) in the original.
    await new Promise((resolve) => setTimeout(resolve, SUBROOM_JOIN_SETTLE_MS));
  }

  await client.send(
    parentRoomId,
    `서브룸 '${req.subRoomName}' 에 작업을 전달했습니다`,
  );
  await client.send(subRoomId, `[DELEGATED] ${req.task}`);

  registerReplyCallback(client, {
    parentRoomId,
    subRoomId,
    subRoomName: req.subRoomName,
  });
}

function registerReplyCallback(
  client: ChatClient,
  ctx: { parentRoomId: string; subRoomId: string; subRoomName: string },
): void {
  const myPids = client.myParticipantIds;
  let fired = false;

  const handler: MessageHandler = async (m: MessageOut) => {
    if (fired) return;
    if (m.room_id !== ctx.subRoomId) return;
    const sender = m.participant_id;
    if (sender && myPids.has(sender)) return;
    fired = true;
    const content = m.content ?? "";
    log.info(
      { sub_room: ctx.subRoomName, content_len: content.length },
      "delegate.reply_captured",
    );
    await client.send(
      ctx.parentRoomId,
      `서브룸 '${ctx.subRoomName}' 결과:\n${content}`,
    );
    client.offMessage(handler);
  };

  client.onMessage(handler);

  // Safety timeout: remove handler + notify if nothing arrived.
  setTimeout(() => {
    if (fired) return;
    log.warn({ sub_room: ctx.subRoomName }, "delegate.reply_timeout");
    // Best effort — swallow errors so a failing timeout notification
    // doesn't bubble up into the runtime.
    void client
      .send(
        ctx.parentRoomId,
        `서브룸 '${ctx.subRoomName}' 에서 5분 내 응답이 없습니다`,
      )
      .catch(() => {});
    client.offMessage(handler);
  }, REPLY_TIMEOUT_MS);
}
