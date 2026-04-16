// Self-echo nonce tracker.
//
// The Python client attaches a UUID ``_nonce`` to every outgoing
// message, then drops incoming frames whose metadata carries a nonce
// we recently emitted. This is the second line of defense (after the
// participant_id hard filter) against seeing our own message come
// back through the pub/sub fan-out.
//
// Implementation note: ``consume`` mutates — once a nonce is observed
// on an incoming frame it's removed, so a replay (rare but possible
// during reconnect) won't double-skip a subsequent legitimate message
// that happens to share the nonce.

import { randomUUID } from "node:crypto";

export class NonceTracker {
  private readonly sent = new Set<string>();

  /**
   * Allocate a fresh nonce and remember it. The agent attaches this
   * to outgoing ``metadata._nonce`` so the server's broadcast echo
   * can be filtered on the way back.
   */
  allocate(): string {
    const n = randomUUID();
    this.sent.add(n);
    return n;
  }

  /**
   * Check-and-remove. Returns true if the nonce was in the sent set
   * (i.e. this is our own echo), false otherwise.
   */
  consume(nonce: string | null | undefined): boolean {
    if (!nonce) return false;
    if (!this.sent.has(nonce)) return false;
    this.sent.delete(nonce);
    return true;
  }

  /** Visible for tests — do not use in production. */
  size(): number {
    return this.sent.size;
  }
}
