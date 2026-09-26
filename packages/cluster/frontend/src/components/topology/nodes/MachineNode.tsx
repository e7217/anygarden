import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import {
  ACCENT,
  BORDER,
  SHADOW_SOFT,
  SURFACE,
  TEXT_MUTED,
  TEXT_PRIMARY,
  machineStatusColor,
} from '../constants'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Machine node: 136×56 rounded card with status dot + name + agent count.
 *
 * Surface, border, shadow, and text resolve through shared theme tokens.
 */
function MachineNodeInner({ data, selected }: NodeProps) {
  const { t } = useLocale()
  const status = (data?.status as string | undefined) ?? 'offline'
  const label = (data?.label as string | undefined) ?? 'machine'
  const agentCount = (data?.agent_count as number | undefined) ?? 0

  const dotColor = machineStatusColor(status)
  const outline = selected ? `2px solid ${ACCENT}` : BORDER

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
      aria-label={t('topology.machineNode', { name: label, status })}
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
        {t(agentCount === 1 ? 'topology.agentCountOne' : 'topology.agentCountMany', { count: agentCount })}
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
