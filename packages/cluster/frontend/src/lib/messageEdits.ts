import type { ChatMessage } from '@/hooks/useWebSocket'
import { apiFetch } from '@/lib/api'

/**
 * MSG-08 — author edit / soft delete of a message.
 *
 * The server keeps the row (same id and seq) and records the change in
 * ``metadata.edited_at`` / ``metadata.deleted_at``. Other tabs learn about
 * it through a ``message_updated`` WS frame; the editing tab publishes the
 * REST response on this window event so the room's message list (owned by
 * ``useWebSocket``) updates without threading callbacks through every
 * bubble container.
 */
export const MESSAGE_UPDATED_EVENT = 'anygarden:message:updated'

// Mirrors ``_STRUCTURED_METADATA_KEYS`` in ``messages/router.py``: rows the
// server refuses to change, so their actions are not offered.
const STRUCTURED_METADATA_KEYS = [
  'system_origin',
  'ingest_only',
  'interaction',
  'interaction_resolution',
  'task_assignment',
  'room_query',
  'room_query_forward',
  'room_query_result',
  'delegation_target_participant_id',
  'delegation_outcome',
] as const

export function isDeletedMessage(message: ChatMessage): boolean {
  return Boolean(message.metadata?.deleted_at)
}

export function isEditedMessage(message: ChatMessage): boolean {
  return Boolean(message.metadata?.edited_at) && !isDeletedMessage(message)
}

export function canChangeMessage(
  message: ChatMessage,
  { isMine, isAgent }: { isMine: boolean; isAgent: boolean },
): boolean {
  if (!isMine || isAgent || isDeletedMessage(message)) return false
  const metadata = message.metadata ?? {}
  return !STRUCTURED_METADATA_KEYS.some(key => key in metadata)
}

/** Replace the message with the same id; unknown ids leave the list as is. */
export function applyMessageUpdate(messages: ChatMessage[], updated: ChatMessage): ChatMessage[] {
  const index = messages.findIndex(message => message.id === updated.id)
  if (index < 0) return messages
  const next = messages.slice()
  next[index] = { ...updated, type: 'message' }
  return next
}

async function readMessage(response: Response): Promise<ChatMessage> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json() as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch { /* keep the status text */ }
    throw new Error(detail)
  }
  return await response.json() as ChatMessage
}

function publish(message: ChatMessage) {
  window.dispatchEvent(new CustomEvent(MESSAGE_UPDATED_EVENT, { detail: message }))
}

export async function editMessage(roomId: string, messageId: string, content: string): Promise<ChatMessage> {
  const response = await apiFetch(
    `/api/v1/rooms/${encodeURIComponent(roomId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'PATCH', body: JSON.stringify({ content }) },
  )
  const updated = await readMessage(response)
  publish(updated)
  return updated
}

export async function deleteMessage(roomId: string, messageId: string): Promise<ChatMessage> {
  const response = await apiFetch(
    `/api/v1/rooms/${encodeURIComponent(roomId)}/messages/${encodeURIComponent(messageId)}`,
    { method: 'DELETE' },
  )
  const updated = await readMessage(response)
  publish(updated)
  return updated
}
