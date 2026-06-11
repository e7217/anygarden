import { useState, type ReactNode } from 'react'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import { Button } from '@/components/ui/button'
import { Menu } from 'lucide-react'

/**
 * PageShell — the shared application frame for full-page surfaces
 * (Admin Machines / Skills / MCP / LLM Gateway / Topology). It owns the
 * left rail (``Sidebar``), the mobile top bar, and the ``<main>`` column
 * so every page has the same rhythm (#435).
 *
 * Before this, each page hand-rolled
 * ``flex h-screen + Sidebar + SidebarExpandButton + main + mobile bar``,
 * which let the mobile-bar title size and content padding drift between
 * pages. Centralizing the shell here is the structural lever for the
 * "unify whitespace / layout" goal.
 *
 * The Room/Chat surface keeps its own bespoke shell (right rail, dvh
 * height, message input) and is a documented exception — see DESIGN.md
 * §5 "Layout Principles".
 */
interface PageShellProps {
  /** Mobile top-bar label. The bar is hidden ≥ md, where the rail is
   *  always visible and pages supply their own header/content. */
  title: string
  children: ReactNode
  /** When true (default) children are wrapped in a single
   *  ``flex-1 overflow-auto`` scroll region. Pages that own their inner
   *  layout — the LLM Gateway secondary rail, Topology's canvas — pass
   *  ``false`` and lay out the remaining space themselves. */
  scroll?: boolean
}

export default function PageShell({ title, children, scroll = true }: PageShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar selectedRoom={null} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <SidebarExpandButton />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* Mobile top bar — hidden on desktop where the rail is pinned. */}
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-white px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <span className="text-sm font-semibold tracking-tight">{title}</span>
        </div>
        {scroll ? <div className="flex-1 overflow-auto">{children}</div> : children}
      </main>
    </div>
  )
}
