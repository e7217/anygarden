/**
 * In-memory composer drafts, keyed by surface.
 *
 * A thread's composer is mounted by whichever layout is active, so
 * switching between the panel and the inline view unmounts one and mounts
 * the other. Without somewhere to park the text, that switch silently
 * discards whatever the user was typing — and comparing the two layouts
 * is the entire point of the toggle, so losing the message mid-comparison
 * is the worst possible moment for it.
 *
 * Deliberately not persisted: a draft should survive a layout switch
 * within a session, not reappear days later in a different tab. It is
 * cleared on send and when the thread is closed.
 */
const drafts = new Map<string, string>()

/** Stable key for a thread's composer, shared by both layouts. */
export function threadDraftKey(rootMessageId: string): string {
  return `thread:${rootMessageId}`
}

export function readDraft(key: string | undefined): string {
  if (!key) return ''
  return drafts.get(key) ?? ''
}

export function writeDraft(key: string | undefined, value: string): void {
  if (!key) return
  if (value === '') drafts.delete(key)
  else drafts.set(key, value)
}

export function clearDraft(key: string | undefined): void {
  if (!key) return
  drafts.delete(key)
}

/** Test seam — drafts are module state that would otherwise leak between cases. */
export function resetDrafts(): void {
  drafts.clear()
}
