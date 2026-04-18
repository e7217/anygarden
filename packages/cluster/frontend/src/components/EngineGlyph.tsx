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
import OpenHands from '@lobehub/icons/es/OpenHands'

interface EngineGlyphProps {
  engine: string | undefined
  /** Pixel size passed to the underlying @lobehub/icons component.
   *  Topology pills render at 16 (the historical default). The avatar
   *  overlay badge renders at 12. */
  size?: number
}

/**
 * Engine → logo component.
 *
 * Order matters: more specific keys (``claude-code``) must be
 * checked before less specific ones (``claude``) even though
 * substring matching already captures the relationship — keeping
 * the explicit order guards against future edits breaking it.
 *
 * Brands without a dedicated @lobehub icon (``deep-agents``) fall
 * back to lucide ``Bot`` so callers always get a visual anchor.
 */
export function EngineGlyph({ engine, size = 16 }: EngineGlyphProps) {
  const e = (engine ?? '').toLowerCase()
  // Anthropic family — Claude Code uses the full Claude color mark;
  // bare ``anthropic`` also routes here since they share branding.
  if (e.includes('claude') || e.includes('anthropic'))
    return <Claude.Color size={size} />
  // OpenAI family — ``codex`` (CLI) and ``openai`` (API) both render
  // the Codex mono mark; there is no ``.Color`` variant for Codex and
  // OpenAI's branding here is dominated by the Codex CLI surface.
  if (e.includes('codex') || e.includes('openai'))
    return <Codex size={size} />
  // Gemini family (``gemini``, ``gemini-cli``).
  if (e.includes('gemini')) return <Gemini.Color size={size} />
  // OpenHands — dedicated brand icon available.
  if (e.includes('openhands')) return <OpenHands.Color size={size} />
  // ``deep-agents`` has no dedicated brand mark in @lobehub/icons
  // v5.4.0 (LangChain/LangGraph exist but deep-agents is a framework
  // on top, not a brand). Falling through to the unknown fallback.
  return <Bot size={size} strokeWidth={1.75} />
}
