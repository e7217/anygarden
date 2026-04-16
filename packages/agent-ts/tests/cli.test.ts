import { describe, it, expect } from "vitest";
import { buildCli, makeAdapter } from "../src/cli.js";
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
