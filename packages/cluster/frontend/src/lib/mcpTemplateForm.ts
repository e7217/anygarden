/**
 * Pure transformations between the simplified MCP template form and the
 * existing `config_per_engine` API shape (#195).
 *
 * The backend keeps a per-engine dict so builtin templates and future
 * engine-specific overrides stay untouched. Admins creating a stdio
 * server almost always want the same config across all three engines —
 * these helpers let the UI present a single form and fan it out at
 * save time.
 */

export const SUPPORTED_ENGINE_IDS = ['claude-code', 'codex-cli', 'gemini-cli'] as const
export type SupportedEngine = (typeof SUPPORTED_ENGINE_IDS)[number]

const PLACEHOLDER_RE = /\$\{([A-Z_][A-Z0-9_]*)\}/g
const STDIO_KEYS = ['command', 'args', 'env'] as const

export interface EnvRow {
  key: string
  secret: boolean
  /** Ignored when `secret` is true — the value becomes `${KEY}`. */
  value: string
}

export interface TemplateFormState {
  slug: string
  displayName: string
  description: string
  command: string
  args: string[]
  envRows: EnvRow[]
}

export interface StdioConfig {
  command: string
  args: string[]
  env: Record<string, string>
}

export interface ApiPayload {
  name: string
  display_name: string
  description: string | null
  icon: string | null
  config_per_engine: Record<SupportedEngine, StdioConfig>
  supported_engines: SupportedEngine[]
  required_env_vars: string[]
}

export interface TemplateInput {
  name: string
  display_name: string
  description: string | null
  config_per_engine: Record<string, Record<string, unknown>>
  required_env_vars: string[]
  supported_engines: string[]
}

export type ParsedTemplate =
  | { mode: 'simple'; state: TemplateFormState }
  | { mode: 'advanced' }

export function slugify(input: string): string {
  const normalised = input
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (normalised) return normalised
  const hash = Math.random().toString(36).slice(2, 10).padEnd(8, '0').slice(0, 8)
  return `custom-${hash}`
}

export function extractPlaceholders(
  sources: (string | Record<string, string>)[],
): string[] {
  const found = new Set<string>()
  const scan = (value: string) => {
    for (const match of value.matchAll(PLACEHOLDER_RE)) {
      found.add(match[1])
    }
  }
  for (const source of sources) {
    if (typeof source === 'string') {
      scan(source)
    } else {
      for (const v of Object.values(source)) {
        if (typeof v === 'string') scan(v)
      }
    }
  }
  return Array.from(found)
}

export function buildTemplatePayload(form: TemplateFormState): ApiPayload {
  const args = form.args.map(a => a.trim()).filter(Boolean)
  const env: Record<string, string> = {}
  const secretKeys: string[] = []
  for (const row of form.envRows) {
    const key = row.key.trim()
    if (!key) continue
    if (row.secret) {
      env[key] = `\${${key}}`
      secretKeys.push(key)
    } else {
      env[key] = row.value
    }
  }
  const config: StdioConfig = { command: form.command.trim(), args, env }
  const placeholders = Array.from(new Set([
    ...secretKeys,
    ...extractPlaceholders([...args, env]),
  ]))
  const description = form.description.trim()
  return {
    name: form.slug,
    display_name: form.displayName,
    description: description || null,
    icon: null,
    config_per_engine: {
      'claude-code': config,
      'codex-cli': config,
      'gemini-cli': config,
    },
    supported_engines: [...SUPPORTED_ENGINE_IDS],
    required_env_vars: placeholders,
  }
}

function looksLikeStdio(block: Record<string, unknown> | undefined): block is {
  command: unknown
  args: unknown
  env: unknown
} {
  if (!block) return false
  const keys = Object.keys(block)
  return keys.length > 0 && keys.every(k => (STDIO_KEYS as readonly string[]).includes(k))
}

function envDictToRows(env: Record<string, unknown>): EnvRow[] {
  return Object.entries(env).map(([key, raw]) => {
    const value = typeof raw === 'string' ? raw : ''
    const isSecret = value === `\${${key}}`
    return {
      key,
      secret: isSecret,
      value: isSecret ? '' : value,
    }
  })
}

export function parseTemplateIntoForm(template: TemplateInput): ParsedTemplate {
  const blocks = SUPPORTED_ENGINE_IDS.map(
    e => template.config_per_engine[e] as Record<string, unknown> | undefined,
  )
  if (blocks.some(b => !b)) return { mode: 'advanced' }
  if (!blocks.every(looksLikeStdio)) return { mode: 'advanced' }

  const [first, ...rest] = blocks as Record<string, unknown>[]
  const firstJson = JSON.stringify(first)
  if (!rest.every(b => JSON.stringify(b) === firstJson)) {
    return { mode: 'advanced' }
  }

  const command = typeof first.command === 'string' ? first.command : ''
  const args = Array.isArray(first.args)
    ? first.args.filter((a): a is string => typeof a === 'string')
    : []
  const env = (first.env && typeof first.env === 'object' && !Array.isArray(first.env))
    ? first.env as Record<string, unknown>
    : {}

  return {
    mode: 'simple',
    state: {
      slug: template.name,
      displayName: template.display_name,
      description: template.description ?? '',
      command,
      args,
      envRows: envDictToRows(env),
    },
  }
}
