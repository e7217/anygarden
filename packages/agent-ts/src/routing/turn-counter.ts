// Per-room agent-only turn counter.
//
// Ported from ``packages/agent/doorae_agent/client.py`` — specifically the
// ``_process_frame`` bookkeeping that prevents runaway agent-to-agent
// loops while still letting task boundaries (``[ROOM_QUERY]`` and
// ``[DELEGATED]``) reset the count.
//
// Issue #67 — self-emitted ``[ROOM_QUERY]``/``[DELEGATED]`` frames also
// reset the counter on the hard/soft filter paths, not just on the
// main processing path. Without that reset a representative agent in
// an agent-only room inherited the previous round's count and got
// silenced after ``max_agent_turns`` turns. The regression test for
// this behaviour lives in ``tests/turn-counter.test.ts``.

export const DEFAULT_MAX_AGENT_TURNS = 6;

/**
 * Return true when ``content`` starts a new task and should reset the
 * per-room agent-only turn counter. Mirrors the Python
 * ``_is_task_init_content`` helper verbatim.
 */
export function isTaskInitContent(content: string): boolean {
  return content.startsWith("[ROOM_QUERY]") || content.startsWith("[DELEGATED]");
}

/**
 * Outcome of processing a message for turn-counting purposes. The
 * caller uses this to decide whether to forward the frame to message
 * handlers. ``"skip_self"`` / ``"skip_nonce"`` / ``"skip_limit"``
 * signal "already handled, don't dispatch"; ``"deliver"`` signals the
 * message should be dispatched.
 */
export type TurnOutcome = "deliver" | "skip_self" | "skip_nonce" | "skip_limit";

export interface TurnDecision {
  outcome: TurnOutcome;
  /** Updated count for the room after this message was processed. */
  count: number;
}

/**
 * Encapsulates the three-path counter update logic from
 * ``_process_frame`` so the ChatClient doesn't have to inline it and
 * tests can drive every branch with plain dict fixtures.
 *
 * Three paths — each must apply the #67 task-init reset:
 *
 *   1. **Hard self-filter** (``senderIsMe``): participant id is ours.
 *   2. **Soft self-filter** (``matchedNonce``): nonce we recently sent.
 *   3. **Main path**: message from another participant. If the sender
 *      carries a ``_nonce`` metadata key it's another agent → count up.
 *      Otherwise it's a human → reset to 0.
 */
export class TurnCounter {
  private readonly counts = new Map<string, number>();
  readonly max: number;

  constructor(max: number = DEFAULT_MAX_AGENT_TURNS) {
    this.max = max;
  }

  getCount(roomId: string): number {
    return this.counts.get(roomId) ?? 0;
  }

  setCount(roomId: string, count: number): void {
    this.counts.set(roomId, count);
  }

  reset(roomId: string): void {
    this.counts.set(roomId, 0);
  }

  /**
   * Hard self-filter bookkeeping. The message came from our own
   * participant id; callers MUST drop the frame after this returns.
   *
   * Issue #67 — if the self-emitted content is a task-init prefix
   * (``[ROOM_QUERY]``/``[DELEGATED]``) reset to 0 so the next
   * incoming reply from another agent starts a fresh budget. Regular
   * self-messages still count toward the limit to bound total
   * agent-only traffic.
   */
  handleSelf(roomId: string, content: string): TurnDecision {
    if (isTaskInitContent(content)) {
      this.setCount(roomId, 0);
    } else {
      this.setCount(roomId, this.getCount(roomId) + 1);
    }
    return { outcome: "skip_self", count: this.getCount(roomId) };
  }

  /**
   * Soft self-filter bookkeeping — the nonce matches a message we
   * recently emitted (participant id missed, e.g. after a reconnect
   * when the server allocated a new participant row). Same task-init
   * reset semantics as ``handleSelf``.
   */
  handleNonceEcho(roomId: string, content: string): TurnDecision {
    if (isTaskInitContent(content)) {
      this.setCount(roomId, 0);
    } else {
      this.setCount(roomId, this.getCount(roomId) + 1);
    }
    return { outcome: "skip_nonce", count: this.getCount(roomId) };
  }

  /**
   * Main-path bookkeeping. A non-self message just arrived.
   *
   * - Task-init prefix → reset to 0 and deliver (new task round).
   * - Sender has a ``_nonce`` → another agent → ``count + 1``. When
   *   the new count exceeds ``max`` return ``skip_limit`` so the
   *   ChatClient bails out before dispatch.
   * - No nonce → human sender → reset to 0 and deliver.
   */
  handleIncoming(
    roomId: string,
    content: string,
    senderHasNonce: boolean,
  ): TurnDecision {
    if (isTaskInitContent(content)) {
      this.setCount(roomId, 0);
      return { outcome: "deliver", count: 0 };
    }
    if (senderHasNonce) {
      const next = this.getCount(roomId) + 1;
      this.setCount(roomId, next);
      if (next > this.max) {
        return { outcome: "skip_limit", count: next };
      }
      return { outcome: "deliver", count: next };
    }
    // Human sender — reset counter, deliver.
    this.setCount(roomId, 0);
    return { outcome: "deliver", count: 0 };
  }
}
