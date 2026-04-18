import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { EngineGlyph } from '@/components/EngineGlyph'
import {
  ENGINE_TINT,
  SHADOW_SOFT,
  TEXT_PRIMARY,
  agentStateColor,
} from '../constants'
import './AgentNode.css'

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
      aria-label={`Agent ${label}, engine ${engine || 'unknown'}, state ${state}`}
      title={`${label} · ${engine} · ${state}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      <div
        className="agent-node__glyph"
        style={{ color: TEXT_PRIMARY }}
        aria-hidden="true"
      >
        <EngineGlyph engine={engine} />
      </div>
      <div className="agent-node__label">{label}</div>
      <div
        className="agent-node__dot"
        style={{ background: ring }}
        aria-hidden="true"
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
