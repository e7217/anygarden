import React from 'react'
import {
  BaseEdge,
  getBezierPath,
  getSmoothStepPath,
  getStraightPath,
  type EdgeProps,
} from '@xyflow/react'
import { edgeStyleFor } from '../constants'

/**
 * Polymorphic relation edge.
 *
 * Reads ``data.kind`` + ``data.actor`` + ``data.is_representative`` and
 * dispatches to the correct path algorithm + style:
 *   - smoothstep for ``owns``, ``places``, ``parent_of``
 *   - straight + dashed for ``participates`` — color-only differentiation
 *     between representative (full Notion Blue) and non-representative
 *     (semi-transparent), so the merged ``represents``/``participates``
 *     model from #226/#228 reads as one kind visually (see #231).
 *
 * Dimming for hover-focus is applied externally via ``style.opacity``
 * on the Edge object, so this component only needs to honor whatever
 * opacity React Flow already merged in for us.
 */
function RelationEdgeInner(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, style: externalStyle } = props
  const kind = (data?.kind as string | undefined) ?? 'owns'
  const actor = data?.actor as 'user' | 'agent' | undefined
  const isRepresentative = Boolean(data?.is_representative)
  const s = edgeStyleFor(kind, actor, isRepresentative)

  // Pick path generator. Fall back to bezier if a future edge kind
  // arrives without a mapping so nothing crashes mid-render.
  let path: string
  let labelX: number
  let labelY: number
  if (s.type === 'straight') {
    ;[path, labelX, labelY] = getStraightPath({ sourceX, sourceY, targetX, targetY })
  } else if (s.type === 'smoothstep') {
    ;[path, labelX, labelY] = getSmoothStepPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    })
  } else {
    ;[path, labelX, labelY] = getBezierPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    })
  }

  void labelX
  void labelY

  return (
    <BaseEdge
      id={id}
      path={path}
      style={{
        stroke: s.stroke,
        strokeWidth: s.strokeWidth,
        strokeDasharray: s.strokeDasharray,
        transition: 'opacity 180ms',
        ...(externalStyle ?? {}),
      }}
    />
  )
}

export const RelationEdge = React.memo(RelationEdgeInner)
