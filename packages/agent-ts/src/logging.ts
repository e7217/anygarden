import pino from "pino";

// Structured logger. Field naming matches Python `structlog` keys used in
// anygarden-agent/anygarden-machine (agent_id, room_id, participant_id, etc.) so
// log aggregation rules built against the Python runtime keep working for
// the TS runtime.
//
// ``VITEST``/``NODE_ENV === "test"`` auto-silence the logger — production
// logs still route through pino at ``info`` by default. Setting
// ``ANYGARDEN_LOG_LEVEL`` wins.
function resolveLogLevel(): string {
  if (process.env.ANYGARDEN_LOG_LEVEL) return process.env.ANYGARDEN_LOG_LEVEL;
  if (process.env.VITEST || process.env.NODE_ENV === "test") return "silent";
  return "info";
}

export const log = pino({
  name: "anygarden-agent-ts",
  level: resolveLogLevel(),
  formatters: {
    level(label) {
      return { level: label };
    },
  },
  timestamp: pino.stdTimeFunctions.isoTime,
});

export type Logger = typeof log;
