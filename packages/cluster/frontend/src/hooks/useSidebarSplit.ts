import { useCallback, useEffect, useState } from 'react'

// Height split between the sidebar's projects and agents sections
// (#928). Only Sidebar reads it, so it is a plain hook rather than a
// context like useSidebarLayout / useRightSidebarLayout.
//
// The split is stored as the agents section's share of the height, not
// as pixels: the sidebar's height varies far more across screens than
// the right rail's width does, and "how I divided it" is what should
// survive moving between a laptop and a monitor. Each section keeps a
// pixel minimum in CSS, so the ratio bounds here only stop the stored
// value from going degenerate.
const RATIO_KEY = 'anygarden_sidebar_split_ratio'
const PROJECTS_KEY = 'anygarden_sidebar_projects_collapsed'
const AGENTS_KEY = 'anygarden_sidebar_agents_collapsed'

export const SIDEBAR_SPLIT = { default: 0.5, min: 0.1, max: 0.9, step: 0.05 } as const

export function clampSplitRatio(ratio: number): number {
  if (!Number.isFinite(ratio)) return SIDEBAR_SPLIT.default
  const bounded = Math.min(SIDEBAR_SPLIT.max, Math.max(SIDEBAR_SPLIT.min, ratio))
  return Math.round(bounded * 1000) / 1000
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    // localStorage throws in private-mode Safari / disabled storage.
    return null
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* ignore */
  }
}

function readInitialRatio(): number {
  const raw = read(RATIO_KEY)
  return raw === null ? SIDEBAR_SPLIT.default : clampSplitRatio(Number(raw))
}

export interface SidebarSplitValue {
  /** Agents section's share of the split height, 0–1. */
  ratio: number
  /** Clamp + persist. Callers commit once per gesture, not per frame. */
  setRatio: (ratio: number) => void
  resetRatio: () => void
  projectsCollapsed: boolean
  toggleProjects: () => void
  agentsCollapsed: boolean
  toggleAgents: () => void
}

export function useSidebarSplit(): SidebarSplitValue {
  const [ratio, setRatioState] = useState(readInitialRatio)
  const [projectsCollapsed, setProjectsCollapsed] = useState(() => read(PROJECTS_KEY) === 'true')
  const [agentsCollapsed, setAgentsCollapsed] = useState(() => read(AGENTS_KEY) === 'true')

  useEffect(() => write(RATIO_KEY, String(ratio)), [ratio])
  useEffect(() => write(PROJECTS_KEY, String(projectsCollapsed)), [projectsCollapsed])
  useEffect(() => write(AGENTS_KEY, String(agentsCollapsed)), [agentsCollapsed])

  const setRatio = useCallback((next: number) => setRatioState(clampSplitRatio(next)), [])
  const resetRatio = useCallback(() => setRatioState(SIDEBAR_SPLIT.default), [])
  const toggleProjects = useCallback(() => setProjectsCollapsed(prev => !prev), [])
  const toggleAgents = useCallback(() => setAgentsCollapsed(prev => !prev), [])

  return {
    ratio,
    setRatio,
    resetRatio,
    projectsCollapsed,
    toggleProjects,
    agentsCollapsed,
    toggleAgents,
  }
}
