import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Bot, Cpu, Zap, Sparkles } from 'lucide-react'
import {
  ENGINE_TINT,
  SHADOW_SOFT,
  TEXT_PRIMARY,
  TEXT_SUBTLE,
  agentStateColor,
} from '../constants'

function engineIcon(engine: string | undefined) {
  const e = (engine ?? '').toLowerCase()
  if (e.includes('codex')) return <Zap size={18} strokeWidth={1.75} />
  if (e.includes('claude')) return <Sparkles size={18} strokeWidth={1.75} />
  if (e.includes('gemini')) return <Cpu size={18} strokeWidth={1.75} />
  return <Bot size={18} strokeWidth={1.75} />
}

/**
 * Agent node: 64×64 circle, engine icon centered, state-colored ring.
 *
 * DESIGN notes:
 * - Border color signals lifecycle state (running → Notion Blue,
 *   crashed → warning orange, idle → soft neutral). This is the only
 *   node where Notion Blue doubles as both status and accent, per
 *   DESIGN.md §2 "Status colors" carveout ("running" == intent is alive).
 * - Background tint is engine-specific but always near-white so the
 *   ring stays the dominant signal.
 */
function AgentNodeInner({ data, selected }: NodeProps) {
  const engine = (data?.engine as string | undefined) ?? ''
  const state = (data?.actual_state as string | undefined) ?? 'idle'
  const label = (data?.label as string | undefined) ?? 'agent'

  const tint = ENGINE_TINT[engine.toLowerCase()] ?? ENGINE_TINT.default
  const ring = agentStateColor(state)
  const borderWidth = selected || state === 'running' ? 2 : 1

  return (
    <div
      style={{
        width: 64,
        height: 64,
        borderRadius: '50%',
        background: tint,
        border: `${borderWidth}px solid ${ring}`,
        boxShadow: SHADOW_SOFT,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        color: TEXT_PRIMARY,
        fontFamily: 'Inter, system-ui, sans-serif',
        transition: 'border-color 180ms, border-width 180ms',
      }}
      aria-label={`Agent ${label}, engine ${engine}, state ${state}`}
      title={`${label} · ${engine} · ${state}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      <div style={{ color: TEXT_PRIMARY, display: 'flex' }}>
        {engineIcon(engine)}
      </div>
      <div
        style={{
          fontSize: 9,
          color: TEXT_SUBTLE,
          marginTop: 1,
          maxWidth: 54,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {engine || 'agent'}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const AgentNode = React.memo(AgentNodeInner)
