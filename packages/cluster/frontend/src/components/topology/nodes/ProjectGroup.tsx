import React from 'react'
import { type NodeProps } from '@xyflow/react'
import { TEXT_MUTED } from '../constants'

/**
 * Project group — dashed-bordered container for rooms that share a
 * project. v1 leaves this hidden by default (dagre lays rooms out in
 * a flat rank) so we don't have to reshuffle positions when the user
 * toggles grouping. Rendered as a React Flow parent node for forwards
 * compatibility — v2 will flip ``node.hidden`` based on a filter toggle.
 */
function ProjectGroupInner({ data }: NodeProps) {
  const label = (data?.label as string | undefined) ?? 'project'
  return (
    <div
      style={{
        minWidth: 320,
        minHeight: 160,
        border: '1px dashed rgba(0,0,0,0.08)',
        borderRadius: 12,
        position: 'relative',
        padding: 12,
        background: 'transparent',
      }}
      aria-label={`Project group ${label}`}
    >
      <span
        style={{
          position: 'absolute',
          top: -8,
          left: 12,
          padding: '0 6px',
          background: '#ffffff',
          color: TEXT_MUTED,
          fontFamily: 'Inter, system-ui, sans-serif',
          fontSize: 11,
          fontWeight: 500,
          letterSpacing: 0.125,
          textTransform: 'uppercase',
        }}
      >
        {label}
      </span>
    </div>
  )
}

export const ProjectGroup = React.memo(ProjectGroupInner)
