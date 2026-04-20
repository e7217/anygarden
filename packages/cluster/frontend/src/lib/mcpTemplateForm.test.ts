import { describe, it, expect } from 'vitest'
import {
  slugify,
  extractPlaceholders,
  buildTemplatePayload,
  parseTemplateIntoForm,
  type TemplateFormState,
  type ParsedTemplate,
} from './mcpTemplateForm'

describe('slugify', () => {
  it('lowercases and kebab-cases ASCII display names', () => {
    expect(slugify('Internal Knowledge Base')).toBe('internal-knowledge-base')
  })

  it('collapses consecutive non-alphanumeric characters into a single dash', () => {
    expect(slugify('Foo   Bar!!!Baz')).toBe('foo-bar-baz')
  })

  it('strips leading and trailing dashes', () => {
    expect(slugify('  Hello World  ')).toBe('hello-world')
  })

  it('falls back to a custom-<hash> slug for non-ASCII display names', () => {
    const out = slugify('한국어 이름')
    expect(out).toMatch(/^custom-[a-z0-9]{8}$/)
  })

  it('falls back to a custom-<hash> slug for an empty input', () => {
    expect(slugify('')).toMatch(/^custom-[a-z0-9]{8}$/)
  })

  it('preserves digits', () => {
    expect(slugify('Server 42')).toBe('server-42')
  })
})

describe('extractPlaceholders', () => {
  it('returns the set of ${VAR} matches from a string list', () => {
    expect(extractPlaceholders(['${A}', 'plain', '${B}'])).toEqual(['A', 'B'])
  })

  it('extracts from env dicts too', () => {
    expect(
      extractPlaceholders([{ TOKEN: '${TOKEN}', FIXED: 'value' }]),
    ).toEqual(['TOKEN'])
  })

  it('deduplicates across sources', () => {
    expect(
      extractPlaceholders(['${A}', { FOO: '${A}', BAR: '${B}' }]),
    ).toEqual(['A', 'B'])
  })

  it('ignores lowercase or malformed placeholders', () => {
    expect(extractPlaceholders(['${lower}', '${With-Dash}'])).toEqual([])
  })

  it('returns [] for empty input', () => {
    expect(extractPlaceholders([])).toEqual([])
  })
})

describe('buildTemplatePayload', () => {
  const baseForm: TemplateFormState = {
    slug: 'test-kb',
    displayName: 'Test KB',
    description: '',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-github'],
    envRows: [
      { key: 'GITHUB_TOKEN', secret: true, value: '' },
    ],
  }

  it('fans out a single config to all three engines', () => {
    const payload = buildTemplatePayload(baseForm)
    expect(payload.supported_engines).toEqual(['claude-code', 'codex', 'gemini-cli'])
    expect(Object.keys(payload.config_per_engine).sort()).toEqual(
      ['claude-code', 'codex', 'gemini-cli'],
    )
    const cfg = payload.config_per_engine['claude-code']
    expect(cfg.command).toBe('npx')
    expect(cfg.args).toEqual(['-y', '@modelcontextprotocol/server-github'])
    expect(cfg.env).toEqual({ GITHUB_TOKEN: '${GITHUB_TOKEN}' })
  })

  it('keeps identical config references across engines so a single JSON.stringify matches', () => {
    const payload = buildTemplatePayload(baseForm)
    const a = JSON.stringify(payload.config_per_engine['claude-code'])
    const b = JSON.stringify(payload.config_per_engine['codex'])
    const c = JSON.stringify(payload.config_per_engine['gemini-cli'])
    expect(a).toBe(b)
    expect(b).toBe(c)
  })

  it('stores non-secret env values verbatim', () => {
    const payload = buildTemplatePayload({
      ...baseForm,
      envRows: [
        { key: 'LOG_LEVEL', secret: false, value: 'info' },
      ],
    })
    expect(payload.config_per_engine['claude-code'].env).toEqual({ LOG_LEVEL: 'info' })
    expect(payload.required_env_vars).toEqual([])
  })

  it('treats secret env rows as ${KEY} placeholders and adds them to required_env_vars', () => {
    const payload = buildTemplatePayload({
      ...baseForm,
      envRows: [
        { key: 'API_KEY', secret: true, value: '' },
      ],
    })
    expect(payload.config_per_engine['claude-code'].env).toEqual({
      API_KEY: '${API_KEY}',
    })
    expect(payload.required_env_vars).toEqual(['API_KEY'])
  })

  it('picks up placeholders from args too (filesystem case)', () => {
    const payload = buildTemplatePayload({
      ...baseForm,
      envRows: [],
      args: ['-y', '@modelcontextprotocol/server-filesystem', '${MCP_FS_ALLOWED_PATH}'],
    })
    expect(payload.required_env_vars).toEqual(['MCP_FS_ALLOWED_PATH'])
  })

  it('merges placeholders from args and secret env rows without duplicates', () => {
    const payload = buildTemplatePayload({
      ...baseForm,
      args: ['${SHARED}'],
      envRows: [
        { key: 'SHARED', secret: true, value: '' },
        { key: 'OTHER', secret: true, value: '' },
      ],
    })
    expect(payload.required_env_vars.sort()).toEqual(['OTHER', 'SHARED'])
  })

  it('trims args and env keys and drops empty ones', () => {
    const payload = buildTemplatePayload({
      ...baseForm,
      args: [' -y ', '', '  '],
      envRows: [
        { key: '  ', secret: false, value: 'ignored' },
        { key: ' FOO ', secret: false, value: 'bar' },
      ],
    })
    expect(payload.config_per_engine['claude-code'].args).toEqual(['-y'])
    expect(payload.config_per_engine['claude-code'].env).toEqual({ FOO: 'bar' })
  })

  it('sets description to null when empty after trim', () => {
    const payload = buildTemplatePayload({ ...baseForm, description: '   ' })
    expect(payload.description).toBeNull()
  })

  it('uses the trimmed description when present', () => {
    const payload = buildTemplatePayload({ ...baseForm, description: '  hello  ' })
    expect(payload.description).toBe('hello')
  })
})

describe('parseTemplateIntoForm', () => {
  const stdioBlock = {
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-github'],
    env: { GITHUB_TOKEN: '${GITHUB_TOKEN}' },
  }

  it('returns simple mode when all three engines have identical stdio blocks', () => {
    const out: ParsedTemplate = parseTemplateIntoForm({
      name: 'github',
      display_name: 'GitHub',
      description: null,
      config_per_engine: {
        'claude-code': stdioBlock,
        'codex': stdioBlock,
        'gemini-cli': stdioBlock,
      },
      required_env_vars: ['GITHUB_TOKEN'],
      supported_engines: ['claude-code', 'codex', 'gemini-cli'],
    })
    expect(out.mode).toBe('simple')
    if (out.mode !== 'simple') throw new Error('typecheck')
    expect(out.state.slug).toBe('github')
    expect(out.state.command).toBe('npx')
    expect(out.state.args).toEqual(['-y', '@modelcontextprotocol/server-github'])
    expect(out.state.envRows).toHaveLength(1)
    expect(out.state.envRows[0]).toEqual({
      key: 'GITHUB_TOKEN',
      secret: true,
      value: '',
    })
  })

  it('returns advanced mode when engines have divergent configs', () => {
    const out = parseTemplateIntoForm({
      name: 'divergent',
      display_name: 'Divergent',
      description: null,
      config_per_engine: {
        'claude-code': { command: 'npx', args: [], env: { FOO: '1' } },
        'codex': { command: 'npx', args: [], env: { FOO: '2' } },
        'gemini-cli': { command: 'npx', args: [], env: { FOO: '1' } },
      },
      required_env_vars: [],
      supported_engines: ['claude-code', 'codex', 'gemini-cli'],
    })
    expect(out.mode).toBe('advanced')
  })

  it('returns advanced mode when a block has non-stdio keys (e.g. HTTP transport)', () => {
    const httpBlock = { type: 'http', url: 'https://example.com' }
    const out = parseTemplateIntoForm({
      name: 'http-template',
      display_name: 'HTTP',
      description: null,
      config_per_engine: {
        'claude-code': httpBlock,
        'codex': httpBlock,
        'gemini-cli': httpBlock,
      },
      required_env_vars: [],
      supported_engines: ['claude-code', 'codex', 'gemini-cli'],
    })
    expect(out.mode).toBe('advanced')
  })

  it('returns advanced mode when engines are missing', () => {
    const out = parseTemplateIntoForm({
      name: 'partial',
      display_name: 'Partial',
      description: null,
      config_per_engine: {
        'claude-code': stdioBlock,
      },
      required_env_vars: [],
      supported_engines: ['claude-code'],
    })
    expect(out.mode).toBe('advanced')
  })

  it('marks an env entry as non-secret when the value is not a ${KEY} placeholder', () => {
    const block = {
      command: 'npx',
      args: [],
      env: { LOG_LEVEL: 'debug' },
    }
    const out = parseTemplateIntoForm({
      name: 'plain-env',
      display_name: 'Plain',
      description: null,
      config_per_engine: {
        'claude-code': block,
        'codex': block,
        'gemini-cli': block,
      },
      required_env_vars: [],
      supported_engines: ['claude-code', 'codex', 'gemini-cli'],
    })
    expect(out.mode).toBe('simple')
    if (out.mode !== 'simple') throw new Error('typecheck')
    expect(out.state.envRows[0]).toEqual({
      key: 'LOG_LEVEL',
      secret: false,
      value: 'debug',
    })
  })
})
