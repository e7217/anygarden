import { useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import { Button } from '@/components/ui/button'
import { Menu } from 'lucide-react'
import { SecondarySidebar } from '@/components/admin-llm-gateway/SecondarySidebar'
import {
  useGatewayModels,
  useGatewaySecrets,
  useGatewayStatus,
} from '@/hooks/useLLMGateway'

/**
 * Route shell for /admin/llm-gateway/* pages.
 *
 * Sets up the three-column layout (main sidebar → secondary sidebar
 * → section <Outlet/>) and owns the cross-section state that the
 * secondary sidebar's Apply footer needs to read:
 *
 * - ``pendingCount`` — currently "number of draft rows whose state
 *   isn't reflected in the running supervisor's config hash". The
 *   server exposes ``status.config_hash`` but doesn't currently
 *   return a separate ``pending_count`` field; we fall back to a
 *   simple "any edit since last Apply" counter maintained by each
 *   section via the ``LLMGatewayContext`` (see below).
 * - ``applying`` / ``onApply`` — the shell owns the Apply mutation
 *   so the footer button works from every sub-route.
 *
 * The sections read their own data via ``useGatewayModels`` /
 * ``useGatewaySecrets`` hooks to keep this shell slim; the hooks'
 * cache is per-hook-instance so each section gets fresh data on
 * navigation.
 */

export default function AdminLLMGatewayPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [pendingCount, setPendingCount] = useState(0)
  const [applying, setApplying] = useState(false)
  const navigate = useNavigate()

  // Owning hooks that the Apply footer and empty-redirect rely on.
  // Sections can call their own hook instances — these are for the
  // shell's own reads.
  const { status, apply, refresh: refreshStatus } = useGatewayStatus(10_000)

  // Nudge sections to also refresh after Apply. Each hook instance
  // holds its own state, so instead of a global store we give
  // sections a "bump" callback via the outlet context.
  const [applyBump, setApplyBump] = useState(0)

  useGatewayModels() // pre-warm so navigating to Models feels instant
  useGatewaySecrets()

  const handleApply = async () => {
    if (applying) return
    setApplying(true)
    try {
      await apply()
      setPendingCount(0)
      setApplyBump(b => b + 1)
      refreshStatus()
    } catch (err) {
      console.error('[llm-gateway] apply failed', err)
      // Leave state intact so the UI still shows pending — admin
      // can retry from the Status panel with more context.
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar
        selectedRoom={null}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <SidebarExpandButton />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* Mobile top bar */}
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-white px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <span className="text-[15px] font-bold tracking-tight">LLM Gateway</span>
        </div>

        <div className="flex min-w-0 flex-1 overflow-hidden">
          <SecondarySidebar
            status={status}
            pendingCount={pendingCount}
            applying={applying}
            onApply={handleApply}
          />
          <div className="flex-1 overflow-auto">
            <Outlet
              context={{
                status,
                incrementPending: () => setPendingCount(c => c + 1),
                resetPending: () => setPendingCount(0),
                applyBump,
                navigateToStatus: () => navigate('/admin/llm-gateway/status'),
              }}
            />
          </div>
        </div>
      </main>
    </div>
  )
}

// Outlet context type — sections import this via useOutletContext.
export interface LLMGatewayOutletContext {
  status: ReturnType<typeof useGatewayStatus>['status']
  incrementPending: () => void
  resetPending: () => void
  /**
   * Bumps on every Apply. Sections listening on this can refresh
   * their own caches so the Status panel's config hash is reflected
   * in model/secret rows that mutated during the operation.
   */
  applyBump: number
  navigateToStatus: () => void
}
