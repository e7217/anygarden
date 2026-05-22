// frontend/src/lib/handoff.ts
//
// Pure helpers for detecting and rendering orchestrator ``[HANDOFF]``
// messages, scrubbing the ``handoff_to: ...`` trailer workers sometimes
// append to their replies, and pattern-matching Claude's automated
// "마이크 넘겼습니다 🎤" status announcements.
//
// Issue #238 — these three protocol artefacts currently leak into the
// chat UI as raw text. ``parseHandoff`` surfaces the structured metadata
// so ``MessageBubble`` can render a dedicated ``HandoffMessageCard``;
// ``stripHandoffToTrailer`` and ``isHandoffStatusMessage`` let the
// caller hide the ambient protocol noise surrounding the handoff.
//
// Server contract (see ``_apply_orchestrator_handoff`` in
// ``packages/cluster/anygarden/ws/handler.py``): an accepted handoff is
// emitted with
//
//   * ``content.startswith('[HANDOFF] ')``                (protocol marker)
//   * ``metadata.next_speaker_participant_id``           (target pid)
//   * ``metadata.mentions`` with a ``type='user'`` entry (target pid)
//
// We require all three to be present before treating a message as a
// handoff, which defends against both legacy messages missing metadata
// and false positives from natural-language occurrences of the prefix.

import type { ChatMessage } from '@/hooks/useWebSocket'

/** Structured projection of an accepted orchestrator handoff. */
export interface HandoffMeta {
  /** Participant id of the agent being handed the turn to. */
  targetParticipantId: string
  /** Handoff body with the ``[HANDOFF]`` prefix and the leading
   * ``<@user:{pid}>`` mention token removed. May be empty when the
   * orchestrator did not supply additional instructions. */
  instruction: string
  /** Verbatim ``metadata.next_speaker_participant_id``. Duplicates
   * ``targetParticipantId`` by construction but is surfaced so
   * callers can cross-check the metadata path independently. */
  nextSpeakerParticipantId: string
}

/** User-mention entry shape in ``metadata.mentions``. Minimal subset
 * the server guarantees — there are other fields but we only need
 * ``type`` and ``id`` here. */
interface UserMention {
  type: 'user'
  id: string
}

function readMentions(meta: unknown): readonly unknown[] {
  if (!meta || typeof meta !== 'object') return []
  const arr = (meta as Record<string, unknown>).mentions
  return Array.isArray(arr) ? arr : []
}

function readNextSpeaker(meta: unknown): string {
  if (!meta || typeof meta !== 'object') return ''
  const v = (meta as Record<string, unknown>).next_speaker_participant_id
  return typeof v === 'string' ? v : ''
}

function firstUserMention(mentions: readonly unknown[]): UserMention | null {
  for (const m of mentions) {
    if (!m || typeof m !== 'object') continue
    const rec = m as Record<string, unknown>
    if (rec.type !== 'user') continue
    const id = rec.id
    if (typeof id === 'string' && id.length > 0) {
      return { type: 'user', id }
    }
  }
  return null
}

/**
 * Return structured handoff metadata when the message satisfies the
 * full three-way server contract, else ``null``.
 *
 * Defensive by design: any of the three conditions missing falls
 * through to the ordinary message render path so legacy/partial
 * payloads never get caught in the handoff variant.
 */
export function parseHandoff(msg: ChatMessage): HandoffMeta | null {
  const content = msg.content ?? ''
  if (!content.startsWith('[HANDOFF]')) return null
  // Require a space OR end-of-prefix after the literal to avoid
  // matching e.g. ``[HANDOFFLIKE]`` — the server emits a single space
  // separator ``[HANDOFF] ...`` so insisting on it keeps false positives
  // tight. Empty body is allowed (``[HANDOFF] `` alone).
  if (content.length > 9 && content[9] !== ' ') return null

  const nextSpeaker = readNextSpeaker(msg.metadata)
  if (!nextSpeaker) return null

  const mention = firstUserMention(readMentions(msg.metadata))
  if (!mention) return null

  // Instruction body: strip the ``[HANDOFF] `` prefix and the first
  // ``<@user:{pid}>`` mention token (we already surface the target in
  // the card header, so repeating the token in the body is noise).
  const withoutPrefix = content.replace(/^\[HANDOFF\]\s*/, '')
  const withoutMention = withoutPrefix.replace(
    /^<@user:[^>]+>\s*/,
    '',
  )
  const instruction = withoutMention.trim()

  return {
    targetParticipantId: mention.id,
    instruction,
    nextSpeakerParticipantId: nextSpeaker,
  }
}

/** Remove the ``[HANDOFF] `` protocol prefix — render-time only, the
 * wire body still carries it so server-side consumers keep working. */
export function stripHandoffPrefix(content: string): string {
  return content.replace(/^\[HANDOFF\]\s*/, '')
}

/**
 * Strip a trailing ``handoff_to: ...`` or Korean "마이크 넘기겠습니다"
 * directive block workers (Codex, Gemini CLI) append to their replies.
 *
 * R2 in the plan — the patterns are end-of-string anchored so
 * mid-body occurrences of the same literals stay intact. If none of
 * the patterns matches we return the input untouched.
 */
export function stripHandoffToTrailer(content: string): string {
  // Pattern A: ``\nhandoff_to: <@user:id>`` with an optional
  // ``participant_id: xxx`` annotation, case-insensitive, anchored to
  // end-of-string. Requires a newline before the trailer so we never
  // eat content when the worker embeds "handoff_to:" inline.
  const patternA =
    /\n+handoff_to:\s*.+?(?:\s+participant_id:\s*[\w-]+)?\s*$/i
  let stripped = content.replace(patternA, '')
  if (stripped !== content) return stripped.trimEnd()

  // Pattern B: Korean "X, 마이크 넘기겠습니다." variant. Matches the
  // full trailing sentence. The worker varies the preamble name so we
  // allow any leading non-newline run before the literal.
  const patternB = /\n+[^\n]*마이크\s*넘기겠습니다\.?\s*$/
  stripped = content.replace(patternB, '')
  if (stripped !== content) return stripped.trimEnd()

  return content
}

/**
 * True when ``content`` matches one of the orchestrator's
 * auto-generated status announcements that accompany a handoff. The
 * caller must additionally verify sender + recency before hiding the
 * message — the pattern alone is only a necessary condition.
 *
 * Pattern set curated from real Claude outputs (issue #238):
 *   - "Codex에게 마이크를 넘겼습니다..."
 *   - "... 응답을 기다리고 있습니다... 🎤"
 *   - "Gemini CLI에게 전달하겠습니다."
 */
export function isHandoffStatusMessage(content: string): boolean {
  if (!content) return false
  if (/에게\s*마이크를\s*넘겼습니다/.test(content)) return true
  if (/기다리고\s*있습니다/.test(content)) return true
  if (/에게\s*전달하겠습니다\.?\s*$/.test(content)) return true
  return false
}
