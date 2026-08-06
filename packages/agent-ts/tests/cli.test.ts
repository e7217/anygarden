import { describe, it, expect, vi } from "vitest";
import { buildCli, makeAdapter, sendAdapterReply } from "../src/cli.js";
import type { ChatClient } from "../src/client.js";
import type { EngineAdapter } from "../src/engines/types.js";

describe("buildCli", () => {
  it("parses the spawner's argv shape", () => {
    const cmd = buildCli();
    cmd.exitOverride().parse(
      ["--engine", "claude_code", "--name", "alpha", "--server", "ws://host"],
      { from: "user" },
    );
    const opts = cmd.opts();
    expect(opts.engine).toBe("claude_code");
    expect(opts.name).toBe("alpha");
    expect(opts.server).toBe("ws://host");
  });

  it("collects --room flags (repeatable)", () => {
    const cmd = buildCli();
    cmd.exitOverride().parse(
      [
        "--engine", "claude_code",
        "--server", "ws://host",
        "--room", "r1",
        "--room", "r2",
      ],
      { from: "user" },
    );
    expect(cmd.opts().room).toEqual(["r1", "r2"]);
  });

  it("errors when --engine or --server is missing", () => {
    const cmd = buildCli();
    expect(() =>
      cmd.exitOverride().parse(["--name", "alpha"], { from: "user" }),
    ).toThrow();
  });
});

describe("makeAdapter", () => {
  it("returns a ClaudeCodeAdapter for engine=claude_code", () => {
    const adapter: EngineAdapter = makeAdapter("claude_code", {
      engine: "claude_code",
      name: "alpha",
      server: "ws://host",
    });
    expect(adapter.constructor.name).toBe("ClaudeCodeAdapter");
  });

  it("throws a clear error for Phase-2 engines (codex, gemini)", () => {
    expect(() =>
      makeAdapter("codex", { engine: "codex", name: "", server: "" }),
    ).toThrow(/out of scope/);
    expect(() =>
      makeAdapter("gemini-cli", { engine: "gemini-cli", name: "", server: "" }),
    ).toThrow(/out of scope/);
  });

  it("throws for unknown engines", () => {
    expect(() =>
      makeAdapter("mystery", { engine: "mystery", name: "", server: "" }),
    ).toThrow(/unknown engine/);
  });
});

describe("sendAdapterReply", () => {
  it("sends an agent answer back to the inbound thread root", async () => {
    const send = vi.fn(async () => undefined);
    const client = { send } as unknown as ChatClient;

    await sendAdapterReply(
      client,
      {
        type: "message",
        id: "reply-id",
        room_id: "room-a",
        participant_id: "human-pid",
        content: "<@user:me-pid> thread question",
        root_message_id: "root-1",
        seq: 2,
        created_at: "2026-08-05T00:00:00Z",
        metadata: null,
      },
      "thread answer",
    );

    expect(send).toHaveBeenCalledWith(
      "room-a",
      "thread answer",
      undefined,
      "root-1",
    );
  });

  it("forwards durable turn lease metadata", async () => {
    const send = vi.fn(async () => undefined);
    const client = { send } as unknown as ChatClient;
    await sendAdapterReply(
      client,
      {
        type: "message",
        id: "message-id",
        room_id: "room-a",
        participant_id: "human-pid",
        content: "question",
        seq: 1,
        created_at: "2026-08-05T00:00:00Z",
        metadata: {
          request_id: "request-1",
          turn_attempt: 2,
          turn_generation: 9,
          turn_lease: "lease-1",
          turn_protocol: 1,
        },
      },
      "answer",
    );
    expect(send).toHaveBeenCalledWith(
      "room-a",
      "answer",
      expect.objectContaining({
        request_id: "request-1",
        turn_attempt: 2,
        turn_generation: 9,
        turn_lease: "lease-1",
      }),
      undefined,
    );
  });
});
