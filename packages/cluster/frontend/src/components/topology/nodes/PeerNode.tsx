import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Globe } from 'lucide-react'
import {
  ACCENT,
  BORDER,
  SHADOW_SOFT,
  SURFACE,
  TEXT_MUTED,
  TEXT_PRIMARY,
  peerStateColor,
} from '../constants'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

const STATE_KEYS: Record<string, MessageKey> = {
  active: 'topology.peerStateActive',
  pending: 'topology.peerStatePending',
  revoked: 'topology.peerStateRevoked',
}

/**
 * Federation peer node (FED-09): 152×56 card with a globe icon, the
 * peer's node id, and its trust state spelled out in text so the state
 * never depends on the status-dot color alone.
 */
function PeerNodeInner({ data, selected }: NodeProps) {
  const { t } = useLocale()
  const state = (data?.state as string | undefined) ?? 'unknown'
  const label = (data?.label as string | undefined) ?? (data?.node_id as string | undefined) ?? 'peer'
  const channelCount = (data?.channel_count as number | undefined) ?? 0
  const stateKey = STATE_KEYS[state]
  const stateLabel = stateKey ? t(stateKey) : state
  const revoked = state === 'revoked'

  return (
    <div
      data-peer-state={state}
      style={{
        width: 152,
        height: 56,
        background: SURFACE,
        border: selected ? `2px solid ${ACCENT}` : BORDER,
        borderStyle: revoked && !selected ? 'dashed' : undefined,
        borderRadius: 10,
        boxShadow: SHADOW_SOFT,
        padding: '8px 12px',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        gap: 2,
        fontFamily: 'Inter, system-ui, sans-serif',
        color: TEXT_PRIMARY,
        opacity: revoked ? 0.75 : 1,
        transition: 'box-shadow 180ms, border-color 180ms',
      }}
      aria-label={t('topology.peerNode', { name: label, state: stateLabel })}
      title={label}
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
        }}
      >
        <Globe aria-hidden size={14} style={{ flex: '0 0 auto', color: TEXT_MUTED }} />
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
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          color: TEXT_MUTED,
          fontWeight: 400,
          whiteSpace: 'nowrap',
        }}
      >
        <span
          aria-hidden
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: peerStateColor(state),
            flex: '0 0 auto',
          }}
        />
        <span>
          {t(channelCount === 1 ? 'topology.peerSummaryOne' : 'topology.peerSummaryMany', {
            state: stateLabel,
            count: channelCount,
          })}
        </span>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ background: 'transparent', border: 'none' }}
      />
    </div>
  )
}

export const PeerNode = React.memo(PeerNodeInner)
