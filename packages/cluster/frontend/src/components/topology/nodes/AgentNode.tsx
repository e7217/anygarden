import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
// Sub-path imports to keep @lobehub/icons tree-shakeable: only the three
// AI-engine logos we actually render are pulled into the bundle.
import Claude from '@lobehub/icons/es/Claude'
import Codex from '@lobehub/icons/es/Codex'
import Gemini from '@lobehub/icons/es/Gemini'
import {
  ENGINE_TINT,
  SHADOW_SOFT,
  TEXT_PRIMARY,
  agentStateColor,
} from '../constants'
import './AgentNode.css'

/**
 * Engine → logo mapping.
 *
 * `engine` values arrive from the backend as identifiers like `codex`,
 * `claude`, `claude-code`, `gemini`, `gemini-cli`. We fold everything
 * to lowercase and substring-match so variants don't need an entry per
 * flavor. Unknown engines fall back to lucide `Bot` so the pill keeps
 * its visual anchor on the left.
 */
export function EngineGlyph({ engine }: { engine: string }) {
  const e = engine.toLowerCase()
  if (e.includes('claude')) return <Claude.Color size={16} />
  if (e.includes('codex')) return <Codex size={16} />
  if (e.includes('gemini')) return <Gemini.Color size={16} />
  return <Bot size={16} strokeWidth={1.75} aria-label="unknown engine" />
}

/**
 * Agent node: 140×44 pill — engine logo + agent name + state dot.
 *
 * DESIGN notes:
 * - Border color signals lifecycle state (running → Notion Blue,
 *   crashed → warning orange, idle → soft neutral). This is the only
 *   node where Notion Blue doubles as both status and accent, per
 *   DESIGN.md §2 "Status colors" carveout ("running" == intent is alive).
 * - Background tint is engine-specific but always near-white so the
 *   state ring stays the dominant signal.
 * - The running pulse (see AgentNode.css) uses box-shadow so it
 *   composes with #82's hover-opacity dimming without conflict.
 */
function AgentNodeInner({ data, selected }: NodeProps) {
  const engine = (data?.engine as string | undefined) ?? ''
  const state = (data?.actual_state as string | undefined) ?? 'idle'
  const label = (data?.label as string | undefined) ?? 'agent'

  const tint = ENGINE_TINT[engine.toLowerCase()] ?? ENGINE_TINT.default
  const ring = agentStateColor(state)
  const borderWidth = selected || state === 'running' ? 2 : 1
  const isRunning = state === 'running'

  const className = isRunning ? 'agent-node agent-node--running' : 'agent-node'

  return (
    <div
      className={className}
      style={{
        background: tint,
        border: `${borderWidth}px solid ${ring}`,
        boxShadow: SHADOW_SOFT,
        color: TEXT_PRIMARY,
      }}
      aria-label={`Agent ${label}, engine ${engine}, state ${state}`}
      title={`${label} · ${engine} · ${state}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      <div className="agent-node__glyph" style={{ color: TEXT_PRIMARY }}>
        <EngineGlyph engine={engine} />
      </div>
      <div className="agent-node__label">{label}</div>
      <div
        className="agent-node__dot"
        style={{ background: ring }}
        aria-hidden
      />
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const AgentNode = React.memo(AgentNodeInner)
