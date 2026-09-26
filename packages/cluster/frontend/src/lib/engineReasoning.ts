import type { EngineCatalog } from '@/hooks/useAgents'

export function reasoningLevelsFor(catalog: EngineCatalog | null, model: string | null | undefined): string[] {
  const selected = catalog?.models.find(item => item.id === (model || catalog.default_model))
  return selected?.reasoning_levels.length ? selected.reasoning_levels : catalog?.reasoning_levels ?? []
}

/** Keep unknown/custom engine constraints intact; clear only known invalid values. */
export function compatibleReasoning(catalog: EngineCatalog | null, model: string | null | undefined, effort: string | null | undefined): string | null {
  const levels = reasoningLevelsFor(catalog, model)
  return effort && (!levels.length || levels.includes(effort)) ? effort : null
}
