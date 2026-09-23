// #687 — mirror the agent adapter's exact CLI version gate in the admin UI so
// a version drift is visible before an agent fails with UNSUPPORTED_RUNTIME.

const VERSION = /\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?/

/** Extract the version token from a raw `--version` banner (e.g. `codex-cli 0.155.1`). */
export function extractEngineVersion(raw: string | null | undefined): string | null {
  return raw?.match(VERSION)?.[0] ?? null
}

/**
 * Warning text when a machine's detected engine version is outside the
 * catalog's `supported_versions`; `null` when supported, ungated or unknown.
 * Comparison is exact, like the adapter (0.85.10 is not 0.85.1).
 */
export function unsupportedEngineVersionWarning(
  engine: string,
  detected: string | null | undefined,
  supported: readonly string[] | undefined,
): string | null {
  if (!supported || supported.length === 0) return null
  const version = extractEngineVersion(detected)
  if (!version || supported.includes(version)) return null
  const required = supported.length === 1 ? supported[0] : `one of ${supported.join(', ')}`
  return `This machine has ${engine} ${version}, but this build requires ${required}. Agents will fail with UNSUPPORTED_RUNTIME until the supported version is installed.`
}
