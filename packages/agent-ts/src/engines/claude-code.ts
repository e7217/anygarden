// Claude Code engine adapter.
//
// Uses the Anthropic ``@anthropic-ai/claude-agent-sdk`` V2 preview
// API (``unstable_v2_createSession`` / ``unstable_v2_resumeSession``).
// V2 is marked ``@alpha`` by the SDK — expect signature drift on
// upgrade and smoke test manually.
//
// Python reference:
// ``packages/agent/anygarden_agent/integrations/claude_code.py``. The
// equivalent Python class keeps one session id per ``room_id`` and
// passes ``resume=<sid>`` on follow-up turns so context persists
// within a room. We do the same here with a per-room
// ``SDKSession`` object.
//
// Credentials: the SDK reads ambient Claude credentials (same as the
// CLI). This adapter never accepts an API key on argv or in config
// and never logs the token.

import type { EngineAdapter } from "./types.js";
import type { MessageOut } from "../protocol/frames.js";
import { withReferencedFilesHint } from "../context/references.js";
import { log } from "../logging.js";

// We load the SDK lazily so tests can ``vi.mock`` it without the
// module resolution happening at import-time (the SDK pulls in native
// ``sharp`` bindings for optional screenshot support which flake in
// CI environments without the right platform variant).
type SDKMessage = {
  type: string;
  message?: { content?: Array<{ type: string; text?: string }> };
  result?: string;
};
export interface SDKSession {
  readonly sessionId: string;
  send(message: string): Promise<void>;
  stream(): AsyncIterable<SDKMessage>;
  close(): void;
}
export interface SDKModule {
  unstable_v2_createSession(options: Record<string, unknown>): SDKSession;
  unstable_v2_resumeSession(sessionId: string, options: Record<string, unknown>): SDKSession;
}

export interface ClaudeCodeAdapterOptions {
  agentName?: string;
  /** Anthropic model id (e.g. ``"claude-sonnet-4-6"``). */
  model?: string;
  /** Working directory passed to the SDK. Defaults to ``process.cwd()``. */
  cwd?: string;
  /**
   * Optional custom SDK loader — tests inject a stub here instead of
   * importing the real ``@anthropic-ai/claude-agent-sdk`` package.
   */
  sdkLoader?: () => Promise<SDKModule>;
  /**
   * Optional callback invoked on every assistant text block as it
   * streams. Used by the CLI to relay typing indicators to the room.
   */
  onStreamChunk?: (text: string) => void;
}

export class ClaudeCodeAdapter implements EngineAdapter {
  private readonly agentName: string;
  private readonly model: string | undefined;
  private readonly cwd: string;
  private readonly sdkLoader: () => Promise<SDKModule>;
  private readonly onStreamChunk?: (text: string) => void;

  private sdk: SDKModule | null = null;
  // Per-room session state: a resumable id (kept across turns) +
  // the live session handle the V2 API returns.
  private readonly sessionIds = new Map<string, string>();
  private readonly sessions = new Map<string, SDKSession>();

  constructor(opts: ClaudeCodeAdapterOptions = {}) {
    this.agentName = opts.agentName ?? "ClaudeCode";
    this.model = opts.model;
    this.cwd = opts.cwd ?? process.cwd();
    this.sdkLoader = opts.sdkLoader ?? defaultSdkLoader;
    this.onStreamChunk = opts.onStreamChunk;
  }

  async start(): Promise<void> {
    try {
      this.sdk = await this.sdkLoader();
      log.info({ agent: this.agentName }, "claude_code.initialized");
    } catch (exc) {
      log.error(
        { error: String(exc) },
        "claude_code.sdk_load_failed",
      );
      this.sdk = null;
    }
  }

  async onMessage(msg: MessageOut): Promise<string | null> {
    if (!this.sdk) return null;
    const content = msg.content ?? "";
    if (!content) return null;
    const roomId = msg.room_id || "_default";
    const prompt = withReferencedFilesHint(msg);

    try {
      const session = this.ensureSession(this.sdk, roomId);
      await session.send(prompt);
      return await this.collectReply(session, roomId);
    } catch (exc) {
      log.error({ error: String(exc), room_id: roomId }, "claude_code.query_failed");
      return null;
    }
  }

  async stop(): Promise<void> {
    for (const session of this.sessions.values()) {
      try {
        session.close();
      } catch {
        // ignore
      }
    }
    this.sessions.clear();
    this.sessionIds.clear();
    this.sdk = null;
  }

  private ensureSession(sdk: SDKModule, roomId: string): SDKSession {
    const existing = this.sessions.get(roomId);
    if (existing) return existing;

    const options: Record<string, unknown> = {
      cwd: this.cwd,
      // Load per-project CLAUDE.md, ``.claude/settings.json``, and
      // user-level rules. Without ``settingSources`` the SDK silently
      // skips them — same footgun as the Python adapter.
      settingSources: ["project", "user"],
    };
    if (this.model !== undefined) {
      options.model = this.model;
    } else {
      // V2 API requires a ``model`` key — the SDK doesn't pick a
      // default from config. Use the most capable mainline model as
      // the fallback; operators can override via the --model flag.
      options.model = "claude-sonnet-4-6";
    }

    const priorSessionId = this.sessionIds.get(roomId);
    const session = priorSessionId
      ? sdk.unstable_v2_resumeSession(priorSessionId, options)
      : sdk.unstable_v2_createSession(options);
    this.sessions.set(roomId, session);
    return session;
  }

  private async collectReply(session: SDKSession, roomId: string): Promise<string | null> {
    const textParts: string[] = [];
    let resultField: string | null = null;

    for await (const message of session.stream()) {
      if (message.type === "assistant") {
        const content = message.message?.content ?? [];
        for (const block of content) {
          if (block.type !== "text") continue;
          const text = block.text;
          if (typeof text === "string" && text.trim().length > 0) {
            textParts.push(text);
            if (this.onStreamChunk) {
              try {
                this.onStreamChunk(text);
              } catch {
                // best-effort — don't let the typing-indicator
                // callback crash the adapter.
              }
            }
          }
        }
      } else if (message.type === "result") {
        // Mirrors the Python ``ResultMessage.result`` preference:
        // when present, it's the authoritative final reply.
        if (typeof message.result === "string" && message.result.trim().length > 0) {
          resultField = message.result;
        }
      }
    }

    // Remember the session id so follow-up turns resume within the
    // same conversation. The SDK populates ``sessionId`` eagerly once
    // the first assistant message flushes.
    try {
      const sid = session.sessionId;
      if (sid) this.sessionIds.set(roomId, sid);
    } catch {
      // ``sessionId`` throws if the session errored before init.
    }

    if (resultField) return resultField.trim();
    if (textParts.length > 0) {
      return textParts.map((p) => p.trim()).join("\n\n").trim();
    }
    return null;
  }
}

async function defaultSdkLoader(): Promise<SDKModule> {
  const mod = (await import("@anthropic-ai/claude-agent-sdk")) as unknown as SDKModule;
  return mod;
}
