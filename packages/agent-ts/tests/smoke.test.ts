import { describe, it, expect } from "vitest";

describe("scaffolding smoke", () => {
  it("runs vitest inside the agent-ts package", () => {
    expect(1 + 1).toBe(2);
  });

  it("loads pino logger without crashing", async () => {
    const { log } = await import("../src/logging.js");
    expect(typeof log.info).toBe("function");
  });
});
