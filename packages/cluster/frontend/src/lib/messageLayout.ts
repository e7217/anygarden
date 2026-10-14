import type { ChatMessage } from '@/hooks/useWebSocket'
import { parseForward, parseResult } from '@/lib/room-query'
import { parseTaskAssignment } from '@/lib/taskAssignment'

/**
 * Whether ``MessageBubble`` draws this message on the right.
 *
 * Only the sender's own plain bubble goes right. Task-assignment,
 * room-query result and forward cards always sit on the left even when
 * the viewer sent them, so anything placed beside a message (the thread
 * reply control) must follow this rather than ``isMine`` alone. Keep it
 * in step with the variant order in ``MessageBubble``.
 */
export function alignsRight(message: ChatMessage, isMine: boolean): boolean {
  if (!isMine) return false
  return !parseTaskAssignment(message) && !parseResult(message) && !parseForward(message)
}
