import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Crown } from 'lucide-react'
import {
  ACCENT,
  BORDER,
  SURFACE,
  TEXT_PRIMARY,
} from '../constants'

/**
 * User node: 56×56 circular avatar.
 *
 * When no avatar asset exists we fall back to the email's first glyph.
 * Admin users get a crown badge — rendered in Notion Blue rather than
 * gold so the accent stays monochromatic per DESIGN.md §2.
 */
function UserNodeInner({ data, selected }: NodeProps) {
  const label = (data?.label as string | undefined) ?? 'user'
  const isAdmin = Boolean(data?.is_admin)

  const initial = (label.match(/[A-Za-z0-9]/)?.[0] ?? '?').toUpperCase()
  const outline = selected ? `1px solid ${ACCENT}` : BORDER

  return (
    <div
      style={{
        width: 56,
        height: 56,
        borderRadius: '50%',
        background: SURFACE,
        border: outline,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: TEXT_PRIMARY,
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 20,
        fontWeight: 600,
        letterSpacing: '-0.3px',
        position: 'relative',
      }}
      aria-label={`User ${label}${isAdmin ? ' (admin)' : ''}`}
      title={label}
    >
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: 'transparent', border: 'none' }}
      />
      {initial}
      {isAdmin && (
        <div
          aria-hidden
          style={{
            position: 'absolute',
            top: -4,
            left: -4,
            width: 18,
            height: 18,
            borderRadius: '50%',
            background: SURFACE,
            border: `1px solid rgba(0,0,0,0.08)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: ACCENT,
          }}
        >
          <Crown size={10} strokeWidth={2} />
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const UserNode = React.memo(UserNodeInner)
