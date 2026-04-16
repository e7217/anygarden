// CLI entrypoint for ``doorae-agent-ts``.
//
// Spawner-compatible: accepts the same flags doorae-machine's Python
// spawner already passes to ``doorae-agent`` so the daemon can swap
// runtimes without changing the argv shape.
//
// Auth: ``DOORAE_TOKEN`` is read from the environment only. Never
// accept the token on argv — mirrors the Python CLI contract.

import { Command } from "commander";
import { ChatClient } from "./client.js";
import { ClaudeCodeAdapter } from "./engines/claude-code.js";
import type { EngineAdapter } from "./engines/types.js";
import type { MessageOut } from "./protocol/frames.js";
import { shouldRespond } from "./routing/should-respond.js";
import { parseDelegate, executeDelegate } from "./coordination/delegate.js";
import { parseRoomQuery, executeRoomQuery } from "./coordination/room-query.js";
import { log } from "./logging.js";

// Bumped manually in lock-step with package.json's version. Kept as a
// literal to avoid a JSON import at runtime (tsup bundles this).
const CLI_VERSION = "0.0.1";

interface CliOptions {
  engine: string;
  name: string;
  server: string;
  room?: string[];
  model?: string;
  reasoningEffort?: string;
}

export function buildCli(): Command {
  const cmd = new Command();
  cmd
    .name("doorae-agent-ts")
    .description("Doorae TypeScript agent runtime (Claude Code MVP)")
    .version(CLI_VERSION)
    .requiredOption("--engine <name>", "engine id (claude_code)")
    .option("--name <name>", "display name for this agent", "")
    .requiredOption("--server <url>", "Doorae cluster WebSocket URL")
    .option("--room <id...>", "room id to join on start (repeatable)", collect, [])
    .option("--model <id>", "override the engine's default model")
    .option("--reasoning-effort <level>", "reasoning effort (low/medium/high)");
  return cmd;
}

function collect(value: string, previous: string[]): string[] {
  previous.push(value);
  return previous;
}

/**
 * Map an ``--engine`` id to an adapter factory. Only ``claude_code``
 * is implemented in this MVP; other ids throw so a misconfigured
 * spawn fails loudly instead of silently doing nothing.
 */
export function makeAdapter(engine: string, opts: CliOptions): EngineAdapter {
  switch (engine) {
    case "claude_code":
      return new ClaudeCodeAdapter({
        agentName: opts.name || "ClaudeCode",
        model: opts.model,
      });
    case "codex":
    case "gemini-cli":
    case "gemini_cli":
      throw new Error(
        `engine '${engine}' is out of scope for the TS runtime MVP (#73 phase 1)`,
      );
    default:
      throw new Error(`unknown engine '${engine}'`);
  }
}

export async function main(argv: string[]): Promise<number> {
  const cli = buildCli();
  cli.parse(argv, { from: "user" });
  const opts = cli.opts<CliOptions>();

  const token = process.env.DOORAE_TOKEN;
  if (!token) {
    log.error("DOORAE_TOKEN env var is required but was not set");
    return 2;
  }

  const client = new ChatClient({
    serverUrl: opts.server,
    token,
    agentName: opts.name,
  });
  let adapter: EngineAdapter;
  try {
    adapter = makeAdapter(opts.engine, opts);
  } catch (exc) {
    log.error({ error: String(exc) }, "cli.adapter_init_failed");
    return 3;
  }
  await adapter.start();

  // Register the main message handler — mirrors
  // ``integrate_with_claude_code`` in Python.
  client.onMessage(async (msg: MessageOut) => {
    if (!shouldRespond(msg, {
      agentName: client.agentName,
      myParticipantIds: client.myParticipantIds,
      agentId: client.agentId,
    })) {
      return;
    }

    // /delegate path
    const delegate = parseDelegate(msg.content ?? "");
    if (delegate) {
      await executeDelegate(client, msg, delegate);
      return;
    }

    // room_query path
    const rq = parseRoomQuery(msg);
    if (rq) {
      await executeRoomQuery(client, msg, rq);
      return;
    }

    // Main LLM call — keep typing indicator alive while we wait.
    const roomId = msg.room_id;
    let typingActive = true;
    const pingTyping = async () => {
      while (typingActive) {
        try {
          await client.sendTyping(roomId, true);
        } catch {
          // best effort
        }
        await new Promise((r) => setTimeout(r, 2_000));
      }
    };
    const typingPromise = pingTyping();
    try {
      const reply = await adapter.onMessage(msg);
      if (reply && reply.trim().length > 0) {
        await client.send(roomId, reply);
      }
    } finally {
      typingActive = false;
      try {
        await client.sendTyping(roomId, false);
      } catch {
        // ignore
      }
      await typingPromise;
    }
  });

  // Join any starter rooms.
  for (const r of opts.room ?? []) {
    await client.joinRoom(r);
  }

  // Graceful shutdown on SIGINT/SIGTERM.
  const shutdown = async (signal: string) => {
    log.info({ signal }, "cli.shutting_down");
    await adapter.stop();
    await client.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  log.info(
    { engine: opts.engine, name: opts.name, server: opts.server, rooms: opts.room ?? [] },
    "cli.started",
  );

  // Keep the process alive. ``run()`` resolves only when all loops
  // exit (e.g. explicit close).
  await client.run();
  return 0;
}

// ``import.meta.url`` vs argv[1] — when invoked as a bin this matches,
// when imported as a library the block is skipped so tests can call
// ``main()`` directly.
const entryUrl = `file://${process.argv[1]}`;
if (import.meta.url === entryUrl) {
  void main(process.argv.slice(2)).then((code) => process.exit(code));
}
