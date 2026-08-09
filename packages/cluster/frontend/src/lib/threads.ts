import type { ChatMessage } from '@/hooks/useWebSocket'

export interface ThreadIndex {
  /** Top-level messages, in stream order. */
  roots: ChatMessage[]
  /** ``root_message_id`` → replies, ordered by ``seq``. */
  repliesByRoot: Map<string, ChatMessage[]>
  /**
   * Ids of entries in ``roots`` that are actually replies whose own root
   * fell outside the loaded window. They are shown so nothing vanishes,
   * but they cannot host a thread: the server rejects a thread rooted at
   * a reply ("Thread root must be a top-level message"), so offering to
   * reply under one would be a button that always fails.
   */
  orphanIds: Set<string>
}

/**
 * Split a room's message stream into its top-level timeline and the
 * thread replies hanging off it.
 *
 * The server enforces a one-level shape: a reply sets both
 * ``parent_message_id`` and ``root_message_id`` to the same top-level
 * message, and the ``ck_messages_thread_shape`` CHECK plus the
 * "Thread root must be a top-level message" guard reject anything
 * nested. So ``root_message_id`` alone classifies a row — we never
 * walk a parent chain.
 *
 * Orphan guard: history loads a bounded window (``limit=100``), so a
 * reply can arrive whose root scrolled out of range. Such a reply is
 * kept in the top-level timeline rather than filed under a root that
 * isn't rendered — otherwise it would silently disappear from the UI.
 */
export function indexThreads(messages: ChatMessage[]): ThreadIndex {
  const rootIds = new Set<string>()
  for (const msg of messages) {
    if (!msg.root_message_id) rootIds.add(msg.id)
  }

  const roots: ChatMessage[] = []
  const repliesByRoot = new Map<string, ChatMessage[]>()
  const orphanIds = new Set<string>()
  for (const msg of messages) {
    const rootId = msg.root_message_id
    if (!rootId || !rootIds.has(rootId)) {
      roots.push(msg)
      // Distinguish "is a top-level message" from "is a reply we had to
      // surface anyway" — callers must not offer to thread the latter.
      if (rootId) orphanIds.add(msg.id)
      continue
    }
    const bucket = repliesByRoot.get(rootId)
    if (bucket) bucket.push(msg)
    else repliesByRoot.set(rootId, [msg])
  }

  // WebSocket delivery and REST backfill can interleave, so order by
  // the server-assigned room sequence rather than arrival.
  for (const bucket of repliesByRoot.values()) {
    bucket.sort((a, b) => a.seq - b.seq)
  }

  return { roots, repliesByRoot, orphanIds }
}

/**
 * True when *messageId* may host a thread. False for an orphaned reply,
 * whose thread lives under a root the client hasn't loaded.
 */
export function canHostThread(index: ThreadIndex, messageId: string): boolean {
  return !index.orphanIds.has(messageId)
}

/** Number of replies filed under *rootId*; 0 when the thread is empty. */
export function replyCount(index: ThreadIndex, rootId: string): number {
  return index.repliesByRoot.get(rootId)?.length ?? 0
}

/** ``created_at`` of the newest reply, or null when there are none. */
export function lastReplyAt(
  index: ThreadIndex,
  rootId: string,
): string | null {
  const bucket = index.repliesByRoot.get(rootId)
  if (!bucket || bucket.length === 0) return null
  return bucket[bucket.length - 1].created_at
}

/**
 * Participant ids that have posted in the thread, root author first,
 * in first-appearance order. Drives the avatar cluster on the reply
 * affordance.
 */
export function threadParticipantIds(
  index: ThreadIndex,
  root: ChatMessage,
): string[] {
  const ordered: string[] = []
  const seen = new Set<string>()
  const push = (pid: string | null | undefined) => {
    if (!pid || seen.has(pid)) return
    seen.add(pid)
    ordered.push(pid)
  }
  push(root.participant_id)
  for (const reply of index.repliesByRoot.get(root.id) ?? []) {
    push(reply.participant_id)
  }
  return ordered
}
