import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import {
  BORDER,
  SHADOW_SOFT,
  SURFACE,
  TEXT_MUTED,
  TEXT_PRIMARY,
  machineStatusColor,
} from '../constants'

/**
 * Machine node: 136×56 rounded card with status dot + name + agent count.
 *
 * Visual style — DESIGN.md §4 Cards: ``1px solid rgba(0,0,0,0.1)``
 * whisper border, warm white surface, sub-0.05 shadow, near-black text.
 */
function MachineNodeInner({ data, selected }: NodeProps) {
  const status = (data?.status as string | undefined) ?? 'offline'
  const label = (data?.label as string | undefined) ?? 'machine'
  const agentCount = (data?.agent_count as number | undefined) ?? 0

  const dotColor = machineStatusColor(status)
  const outline = selected ? '1px solid #0075de' : BORDER

  return (
    <div
      style={{
        width: 136,
        height: 56,
        background: SURFACE,
        border: outline,
        borderRadius: 10,
        boxShadow: SHADOW_SOFT,
        padding: '8px 12px',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        gap: 2,
        fontFamily: 'Inter, system-ui, sans-serif',
        color: TEXT_PRIMARY,
        transition: 'box-shadow 180ms, border-color 180ms',
      }}
      aria-label={`Machine ${label}, status ${status}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 13,
          fontWeight: 500,
          letterSpacing: '-0.1px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        <span
          aria-hidden
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: dotColor,
            flex: '0 0 auto',
          }}
        />
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {label}
        </span>
      </div>
      <div
        style={{
          fontSize: 11,
          color: TEXT_MUTED,
          fontWeight: 400,
        }}
      >
        {agentCount} {agentCount === 1 ? 'agent' : 'agents'}
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const MachineNode = React.memo(MachineNodeInner)
