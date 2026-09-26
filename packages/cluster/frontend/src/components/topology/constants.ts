/**
 * Topology design tokens.
 *
 * Resolve through the shared semantic tokens so graph nodes and edges
 * follow the light and dark themes without re-rendering graph data.
 */

export const ACCENT = 'var(--color-brand-text)'
export const ACCENT_SOFT = 'var(--color-topology-accent-soft)'

// Node health stays distinct from the teal action accent.
export const STATUS_ONLINE = 'var(--color-status-online)'
export const STATUS_OFFLINE = 'var(--color-foreground-subtle)'
export const STATUS_DRAIN = 'var(--color-warning)'

// Shared surface borders.
export const BORDER = '1px solid var(--color-border)'
export const BORDER_SOFT = '1px solid var(--color-border-subtle)'

export const SHADOW_SOFT = 'var(--shadow-card)'

// Surfaces.
export const SURFACE = 'var(--color-surface-elevated)'
export const SURFACE_ALT = 'var(--color-surface-alt)'

// Text.
export const TEXT_PRIMARY = 'var(--color-foreground)'
export const TEXT_MUTED = 'var(--color-foreground-muted)'
export const TEXT_SUBTLE = 'var(--color-foreground-subtle)'

// Engine-specific tints use the paired theme palette.
export const ENGINE_TINT: Record<string, string> = {
  codex: 'var(--color-tone-8)',
  claude: 'var(--color-tone-4)',
  gemini: 'var(--color-tone-6)',
  openai: 'var(--color-tone-3)',
  default: 'var(--color-surface-alt)',
}

/** Agent actual_state → border color (status dot / focus ring). */
export function agentStateColor(state: string | undefined | null): string {
  if (!state) return STATUS_OFFLINE
  if (state === 'running') return ACCENT
  if (state === 'starting' || state === 'stopping') return TEXT_SUBTLE
  if (state === 'crashed') return 'var(--color-danger)'
  if (state === 'idle' || state === 'stopped') return 'var(--color-border-strong)'
  return STATUS_OFFLINE
}

/** Machine status → status dot color. */
export function machineStatusColor(status: string | undefined): string {
  if (status === 'online') return STATUS_ONLINE
  if (status === 'draining') return STATUS_DRAIN
  return STATUS_OFFLINE
}

/** Edge kind → React Flow edge style */
export interface EdgeStyle {
  stroke: string
  strokeWidth: number
  strokeDasharray?: string
  type?: 'smoothstep' | 'straight' | 'default'
}

export function edgeStyleFor(
  kind: string,
  actor?: 'user' | 'agent',
  isRepresentative?: boolean,
): EdgeStyle {
  switch (kind) {
    case 'owns':
      return { stroke: 'var(--color-border-strong)', strokeWidth: 1, type: 'smoothstep' }
    case 'places':
      return { stroke: 'var(--color-foreground-subtle)', strokeWidth: 1.5, type: 'smoothstep' }
    case 'participates':
      // Same shape across the whole participates kind — straight + dashed —
      // so the merged ``represents``/``participates`` model from #226/#228
      // reads as a single domain relation. The representative case only
      // swaps ACCENT_SOFT for the full theme accent so
      // differentiation is color-only per #231.
      if (actor === 'agent') {
        return {
          stroke: isRepresentative ? ACCENT : ACCENT_SOFT,
          strokeWidth: 1,
          strokeDasharray: '3 3',
          type: 'straight',
        }
      }
      return {
        stroke: 'var(--color-border-strong)',
        strokeWidth: 1,
        strokeDasharray: '4 4',
        type: 'straight',
      }
    case 'parent_of':
      return {
        stroke: 'var(--color-foreground-subtle)',
        strokeWidth: 1.5,
        type: 'smoothstep',
      }
    default:
      return { stroke: 'var(--color-border-strong)', strokeWidth: 1, type: 'smoothstep' }
  }
}
