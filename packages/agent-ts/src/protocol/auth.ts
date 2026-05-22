// JWT subprotocol for authenticating the agent WS connection.
//
// The server expects two subprotocol tokens:
//   Sec-WebSocket-Protocol: anygarden.v1, bearer.<token>
//
// Mirrors packages/agent/anygarden_agent/protocol/versioning.py. Kept tiny
// on purpose so the server can evolve the scheme without dragging the
// WS client refactor along.

export const PROTOCOL_VERSION = "v1";
export const SUBPROTOCOL = "anygarden.v1";

/**
 * Build the subprotocol list that `ws.connect` uses for bearer auth.
 * Returns ``[SUBPROTOCOL, "bearer.<token>"]`` — the same shape the
 * Python SDK produces.
 */
export function buildSubprotocols(token: string): string[] {
  return [SUBPROTOCOL, `bearer.${token}`];
}

/**
 * Protocol-version compatibility check. The server sends its version
 * on the welcome frame; this helper lives here so the client doesn't
 * have to import it separately.
 */
export function isCompatible(serverVersion: string): boolean {
  return serverVersion === PROTOCOL_VERSION;
}
