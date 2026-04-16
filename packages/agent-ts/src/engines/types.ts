// Engine adapter interface — bridges Doorae chat messages to an LLM
// engine and returns the reply (or null to skip).
//
// Mirrors the Python ``EngineAdapter`` abstract class in
// ``packages/agent/doorae_agent/integrations/base.py``. Concrete
// adapters live in ``src/engines/<name>.ts``.

import type { MessageOut } from "../protocol/frames.js";

export interface EngineAdapter {
  /** Initialize the engine (SDK handshake, auth check, etc.). */
  start(): Promise<void>;
  /**
   * Process one incoming message. Return the reply string to send
   * back to the room, or ``null`` to stay silent.
   */
  onMessage(msg: MessageOut): Promise<string | null>;
  /** Tear down — close any sessions, free SDK resources. */
  stop(): Promise<void>;
}
