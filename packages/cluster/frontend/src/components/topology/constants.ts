/**
 * Topology design tokens.
 *
 * Pulled FROM DESIGN.md so the node/edge components stay visually
 * consistent with the rest of the app. Do not hard-code colors,
 * borders, or shadows in component files — reach for these constants
 * and extend here when a new variant is needed.
 */

// Notion Blue — the single saturated interactive accent per DESIGN.md §2.
export const ACCENT = '#0075de'
export const ACCENT_SOFT = '#0075de80' // 50% alpha for agent-participates edge

// Status color split per DESIGN.md §2 "Status colors".
export const STATUS_ONLINE = '#5b9e6d' // muted sage
export const STATUS_OFFLINE = 'rgba(0,0,0,0.25)'
export const STATUS_DRAIN = '#dd5b00' // warning orange, used sparingly

// Whisper-weight borders.
export const BORDER = '1px solid rgba(0,0,0,0.1)'
export const BORDER_SOFT = '1px solid rgba(0,0,0,0.08)'

// Sub-0.05 shadow stack (matches DESIGN.md Soft Card / Level 2).
export const SHADOW_SOFT = '0 1px 2px rgba(0,0,0,0.04)'

// Surfaces.
export const SURFACE = '#ffffff'
export const SURFACE_ALT = '#f6f5f4' // warm white

// Text.
export const TEXT_PRIMARY = 'rgba(0,0,0,0.95)'
export const TEXT_MUTED = '#615d59'
export const TEXT_SUBTLE = '#a39e98'

// Engine-specific background tints. Keep these near-white so the
// saturation never fights Notion Blue — these are flavor, not signal.
export const ENGINE_TINT: Record<string, string> = {
  codex: '#eff6ff', // blue-50
  claude: '#fff7ed', // orange-50
  gemini: '#f5f3ff', // violet-50
  openai: '#ecfdf5', // emerald-50
  default: '#f6f5f4', // warm white
}

/** Agent actual_state → border color (status dot / focus ring). */
export function agentStateColor(state: string | undefined | null): string {
  if (!state) return STATUS_OFFLINE
  if (state === 'running') return ACCENT
  if (state === 'starting' || state === 'stopping') return '#a39e98'
  if (state === 'crashed') return '#dd5b00'
  if (state === 'idle' || state === 'stopped') return 'rgba(0,0,0,0.15)'
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
): EdgeStyle {
  switch (kind) {
    case 'owns':
      return { stroke: 'rgba(0,0,0,0.2)', strokeWidth: 1, type: 'smoothstep' }
    case 'places':
      return { stroke: 'rgba(0,0,0,0.28)', strokeWidth: 1.5, type: 'smoothstep' }
    case 'participates':
      if (actor === 'agent') {
        return {
          stroke: ACCENT_SOFT,
          strokeWidth: 1,
          strokeDasharray: '3 3',
          type: 'straight',
        }
      }
      return {
        stroke: 'rgba(0,0,0,0.15)',
        strokeWidth: 1,
        strokeDasharray: '4 4',
        type: 'straight',
      }
    case 'parent_of':
      return {
        stroke: 'rgba(0,0,0,0.22)',
        strokeWidth: 1.5,
        type: 'smoothstep',
      }
    case 'represents':
      return { stroke: ACCENT, strokeWidth: 2, type: 'smoothstep' }
    default:
      return { stroke: 'rgba(0,0,0,0.2)', strokeWidth: 1, type: 'smoothstep' }
  }
}
