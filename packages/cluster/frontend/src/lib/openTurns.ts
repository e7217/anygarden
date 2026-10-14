/** ENG-09 — an open general-room turn, as ``GET /api/v1/turns/open`` lists it. */
export interface OpenTurn {
  request_id: string
  participant_id: string | null
  /** Root of the thread the turn answers in; null for the main timeline. */
  thread_root_id: string | null
  state: string
}

/** States in which the agent is actually producing the answer. */
const RUNNING_STATES = new Set(['leased', 'completing'])

/**
 * The turn each participant is working on right now: the running one when
 * there is one, otherwise the oldest queued one (the list is oldest first).
 */
export function activeTurnByParticipant(turns: OpenTurn[]): Map<string, OpenTurn> {
  const active = new Map<string, OpenTurn>()
  for (const turn of turns) {
    const pid = turn.participant_id
    if (!pid) continue
    const current = active.get(pid)
    if (!current || (!RUNNING_STATES.has(current.state) && RUNNING_STATES.has(turn.state))) {
      active.set(pid, turn)
    }
  }
  return active
}

/**
 * Splits working participants into those answering in the main timeline
 * and those answering inside a thread, keyed by the thread's root id.
 *
 * ``canShowThread`` says whether a thread can display its own work; when it
 * cannot (its root is not loaded), the participant stays in the main
 * timeline so the work never disappears from view.
 */
export function placeWorkingParticipants(
  participantIds: string[],
  turns: OpenTurn[],
  canShowThread: (rootId: string) => boolean,
): { main: string[]; byThread: Map<string, string[]> } {
  const active = activeTurnByParticipant(turns)
  const main: string[] = []
  const byThread = new Map<string, string[]>()
  for (const pid of participantIds) {
    const rootId = active.get(pid)?.thread_root_id
    if (rootId && canShowThread(rootId)) {
      byThread.set(rootId, [...(byThread.get(rootId) ?? []), pid])
    } else {
      main.push(pid)
    }
  }
  return { main, byThread }
}

/** Request ids of every open turn in a thread — what "stop thread" cancels. */
export function threadTurnIds(turns: OpenTurn[], rootId: string, participantId?: string): string[] {
  return turns
    .filter(turn => turn.thread_root_id === rootId && (!participantId || turn.participant_id === participantId))
    .map(turn => turn.request_id)
}
