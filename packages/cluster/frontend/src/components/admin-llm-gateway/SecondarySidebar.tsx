import { Link, useLocation } from 'react-router-dom'
import { Boxes, Key, Activity, BarChart3, RefreshCw } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { GatewayStatus } from '@/hooks/useLLMGateway'

/**
 * Left rail for the /admin/llm-gateway pages.
 *
 * Holds navigation to the 4 sections plus a fixed footer with
 * gateway status + "Apply changes" button. The button is the
 * single action that turns draft DB edits into a supervised
 * respawn — §12.3 of the design doc. Pending count is computed
 * cheaply client-side (config_hash comparison) by the caller.
 */

const SECTIONS: Array<{
  path: string
  label: string
  Icon: React.ComponentType<{ className?: string }>
  hint: string
}> = [
  {
    path: 'models',
    label: 'Models',
    Icon: Boxes,
    hint: 'Register models that agents can request.',
  },
  {
    path: 'secrets',
    label: 'Secrets',
    Icon: Key,
    hint: 'API keys for upstream providers.',
  },
  {
    path: 'status',
    label: 'Status',
    Icon: Activity,
    hint: 'Subprocess state, config hash, restart.',
  },
  {
    path: 'usage',
    label: 'Usage',
    Icon: BarChart3,
    hint: 'Recent request counts by model and agent.',
  },
]

interface Props {
  status: GatewayStatus | null
  pendingCount: number
  applying: boolean
  onApply: () => void
}

export function SecondarySidebar({
  status, pendingCount, applying, onApply,
}: Props) {
  const location = useLocation()
  const stateColor = statusDotColor(status?.state)
  const stateLabel = status?.state
    ? status.state.charAt(0).toUpperCase() + status.state.slice(1)
    : 'Disabled'

  return (
    <aside className="flex w-[240px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-background)]">
      <div className="border-b border-[var(--color-border)] px-4 py-3">
        <p className="text-badge uppercase text-[var(--color-foreground-muted)]">
          LLM Gateway
        </p>
        <p className="mt-1 text-[13px] leading-snug text-[var(--color-foreground-muted)]">
          Route all agent LLM calls through a single supervised endpoint.
        </p>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-2">
        <p className="text-badge uppercase px-2 py-1 text-[var(--color-foreground-muted)]">
          Configuration
        </p>
        <div className="flex flex-col gap-0.5">
          {SECTIONS.slice(0, 2).map(section => (
            <NavItem
              key={section.path}
              to={`/admin/llm-gateway/${section.path}`}
              label={section.label}
              Icon={section.Icon}
              active={location.pathname.endsWith(`/${section.path}`)}
            />
          ))}
        </div>

        <p className="text-badge uppercase px-2 py-1 pt-3 text-[var(--color-foreground-muted)]">
          Runtime
        </p>
        <div className="flex flex-col gap-0.5">
          {SECTIONS.slice(2).map(section => (
            <NavItem
              key={section.path}
              to={`/admin/llm-gateway/${section.path}`}
              label={section.label}
              Icon={section.Icon}
              active={location.pathname.endsWith(`/${section.path}`)}
              statusDot={section.path === 'status' ? stateColor : undefined}
            />
          ))}
        </div>
      </nav>

      <div className="border-t border-[var(--color-border)] px-3 py-3">
        <div className="mb-2 flex items-center justify-between text-[12px]">
          <span className="text-[var(--color-foreground-muted)]">Status</span>
          <span className="flex items-center gap-1.5 text-[var(--color-foreground)]">
            <span
              className={cn('h-1.5 w-1.5 rounded-full', stateColor)}
              aria-hidden
            />
            {stateLabel}
          </span>
        </div>
        {pendingCount > 0 && (
          <p className="mb-2 text-[12px] text-[var(--color-foreground-muted)]">
            {pendingCount} pending change{pendingCount === 1 ? '' : 's'}
          </p>
        )}
        <Button
          onClick={onApply}
          disabled={pendingCount === 0 || applying}
          className="w-full justify-center"
        >
          {applying ? (
            <>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              Applying…
            </>
          ) : pendingCount > 0 ? (
            `Apply${pendingCount > 0 ? ` (${pendingCount})` : ''}`
          ) : (
            'No changes to apply'
          )}
        </Button>
      </div>
    </aside>
  )
}

interface NavItemProps {
  to: string
  label: string
  Icon: React.ComponentType<{ className?: string }>
  active: boolean
  statusDot?: string
}

function NavItem({ to, label, Icon, active, statusDot }: NavItemProps) {
  return (
    <Link
      to={to}
      className={cn(
        'flex items-center rounded-[var(--radius-sm)] px-2 py-1.5 text-[14px] font-medium transition-colors',
        active
          ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
          : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
      )}
    >
      <Icon className="mr-2 h-4 w-4 text-[var(--color-foreground-subtle)]" />
      {label}
      {statusDot && (
        <span
          className={cn('ml-auto h-1.5 w-1.5 rounded-full', statusDot)}
          aria-hidden
        />
      )}
    </Link>
  )
}

function statusDotColor(state?: string): string {
  switch (state) {
    case 'running':
      return 'bg-emerald-500'
    case 'starting':
    case 'restarting':
      return 'bg-blue-500'
    case 'crashed':
      return 'bg-amber-500'
    case 'failed':
      return 'bg-red-500'
    default:
      return 'bg-[rgba(0,0,0,0.25)]'
  }
}
