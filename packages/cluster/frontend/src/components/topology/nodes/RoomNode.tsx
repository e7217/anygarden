import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Star } from 'lucide-react'
import {
  ACCENT,
  BORDER,
  SURFACE,
  TEXT_MUTED,
  TEXT_PRIMARY,
} from '../constants'

/**
 * Room node: auto-width pill, 32px tall, rounded-full.
 *
 * Channels use ``#`` prefix; DMs use ``@``. Representative-agent rooms
 * get a leading star rendered in Notion Blue to flag the relationship
 * at a glance (the actual "represents" edge is also drawn, but the
 * star disambiguates without needing to hover).
 */
function RoomNodeInner({ data, selected }: NodeProps) {
  const label = (data?.label as string | undefined) ?? 'room'
  const isDm = Boolean(data?.is_dm)
  const participantCount = (data?.participant_count as number | undefined) ?? 0
  const representative = Boolean(data?.representative_agent_id)

  const prefix = isDm ? '@' : '#'
  const outline = selected ? `1px solid ${ACCENT}` : BORDER

  return (
    <div
      style={{
        height: 32,
        background: SURFACE,
        border: outline,
        borderRadius: 9999,
        padding: '0 12px',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        color: TEXT_PRIMARY,
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 12.5,
        fontWeight: 500,
        letterSpacing: '-0.05px',
        maxWidth: 220,
      }}
      aria-label={`Room ${prefix}${label}, ${participantCount} participants`}
      title={`${prefix}${label} · ${participantCount}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      {representative && (
        <Star
          size={12}
          strokeWidth={1.5}
          aria-hidden
          style={{ color: ACCENT, flex: '0 0 auto' }}
        />
      )}
      <span
        aria-hidden
        style={{ color: TEXT_MUTED, fontWeight: 400, flex: '0 0 auto' }}
      >
        {prefix}
      </span>
      <span
        style={{
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          flex: '0 1 auto',
        }}
      >
        {label}
      </span>
      <span
        style={{
          color: TEXT_MUTED,
          fontWeight: 400,
          fontSize: 11,
          flex: '0 0 auto',
          marginLeft: 4,
        }}
      >
        · {participantCount}
      </span>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const RoomNode = React.memo(RoomNodeInner)
