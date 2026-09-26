export interface Participant {
  id: string
  display_name: string
  kind: string
  user_id?: string
  agent_id?: string
  // Mirrors ``ParticipantOut.role`` from ``rooms/router.py``.
  // Used for per-room admin-ish UI gating (e.g. the Invites button)
  // — the server remains the sole authority.
  role?: string
  // True for anonymous guest users. Lets the UI show a distinct
  // "guest" badge without having to introduce a new ``kind`` value,
  // which would break legacy callers expecting ``user``/``agent``.
  is_anonymous?: boolean
  // Presence fields (#54). Populated from ``GET /rooms/{id}`` and
  // merged in realtime via ``useParticipantPresence`` WS patches.
  online?: boolean
  last_seen_at?: string | null
  // Agent engine identifier (#102). Populated when ``kind === 'agent'``
  // from the backing ``Agent.engine`` row; undefined for user/guest.
  // Drives the engine-mark badge on ``EntityAvatar`` (available to
  // non-admin viewers too, unlike the admin-gated ``useAgents()``).
  engine?: string
  // Issue #101 — agent avatar override (null for user participants).
  avatar_kind?: string | null
  avatar_value?: string | null
  // Issue #271 — short public-facing self-introduction. Populated for
  // agent participants whose admin set ``Agent.description``; null
  // for users/guests and for agents without a description set.
  description?: string | null
}
