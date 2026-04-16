import React from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
// Sub-path imports to keep @lobehub/icons tree-shakeable: only the
// AI-engine logos we actually render are pulled into the bundle.
// Mapping covers every engine ID in
// packages/agent/doorae_agent/integrations/__init__.py::ENGINES
// (claude-code, codex, gemini-cli, openhands, deep-agents, openai,
// anthropic) plus backend-agnostic variants the UI might still see.
import Claude from '@lobehub/icons/es/Claude'
import Codex from '@lobehub/icons/es/Codex'
import Gemini from '@lobehub/icons/es/Gemini'
import OpenAI from '@lobehub/icons/es/OpenAI'
import Anthropic from '@lobehub/icons/es/Anthropic'
import OpenHands from '@lobehub/icons/es/OpenHands'
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
 * The backend emits engine identifiers from
 * ``doorae_agent.integrations.ENGINES``: ``claude-code``, ``codex``,
 * ``gemini-cli``, ``openhands``, ``deep-agents``, ``openai``,
 * ``anthropic``. We fold to lowercase and substring-match so
 * CLI/SDK flavor variants share a single branch.
 *
 * Order matters: more specific keys (``claude-code``) must be
 * checked before less specific ones (``claude``) even though
 * substring matching already captures the relationship — keeping
 * the explicit order guards against future edits breaking it.
 *
 * Brands without a dedicated @lobehub icon (deep-agents) fall back
 * to lucide ``Bot`` so the pill keeps its visual anchor on the left.
 */
export function EngineGlyph({ engine }: { engine: string }) {
  const e = engine.toLowerCase()
  // Anthropic family — Claude Code uses the full Claude color mark;
  // bare ``anthropic`` also routes here since they share branding.
  if (e.includes('claude') || e.includes('anthropic'))
    return <Claude.Color size={16} />
  // OpenAI family — ``codex`` (CLI) and ``openai`` (API) both render
  // the Codex mono mark; there is no ``.Color`` variant for Codex and
  // OpenAI's branding here is dominated by the Codex CLI surface.
  if (e.includes('codex') || e.includes('openai'))
    return <Codex size={16} />
  // Gemini family (``gemini``, ``gemini-cli``).
  if (e.includes('gemini')) return <Gemini.Color size={16} />
  // OpenHands — dedicated brand icon available.
  if (e.includes('openhands')) return <OpenHands.Color size={16} />
  // ``deep-agents`` has no dedicated brand mark in @lobehub/icons
  // v5.4.0 (LangChain/LangGraph exist but deep-agents is a framework
  // on top, not a brand). Falling through to the unknown fallback.
  return <Bot size={16} strokeWidth={1.75} />
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
