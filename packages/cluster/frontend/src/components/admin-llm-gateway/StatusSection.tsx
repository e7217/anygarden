import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { RefreshCw, RotateCcw, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useGatewayStatus } from '@/hooks/useLLMGateway'
import type { LLMGatewayOutletContext } from '@/pages/AdminLLMGatewayPage'
import { cn } from '@/lib/utils'

/**
 * Runtime inspection + control panel for the supervised litellm.
 * Pulls the same status hook the shell's Apply footer uses so
 * numbers stay consistent across the layout.
 */

export function StatusSection() {
  const { status, loadState, error, refresh, apply, restart } =
    useGatewayStatus(5_000)
  const { resetPending } = useOutletContext<LLMGatewayOutletContext>()
  const [applying, setApplying] = useState(false)
  const [restarting, setRestarting] = useState(false)

  const doApply = async () => {
    if (applying) return
    setApplying(true)
    try {
      await apply()
      resetPending()
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  const doRestart = async () => {
    if (restarting) return
    if (!window.confirm('Hard-restart the gateway? This briefly interrupts any in-flight requests.')) return
    setRestarting(true)
    try {
      await restart()
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err))
    } finally {
      setRestarting(false)
    }
  }

  const state = status?.state ?? 'disabled'
  const tint = stateTint(state)

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <header className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-[22px] font-bold tracking-tight text-[var(--color-foreground)]">
            Status
          </h1>
          <p className="mt-1 text-[13px] text-[var(--color-foreground-muted)]">
            Gateway subprocess state and runtime controls.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={refresh}>
          <RefreshCw className={cn('mr-1 h-3.5 w-3.5', loadState === 'loading' && 'animate-spin')} />
          Refresh
        </Button>
      </header>

      {loadState === 'error' && status === null && (
        <div className="mb-4 rounded-[var(--radius-md)] border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/10 px-3 py-2 text-[13px] text-[var(--color-destructive)]">
          Couldn't load status: {error}
        </div>
      )}

      {/* Status card */}
      <div className={cn(
        'rounded-[var(--radius-md)] border p-5 shadow-whisper',
        tint.border, tint.bg,
      )}>
        <div className="mb-3 flex items-center gap-2">
          <span className={cn('h-2 w-2 rounded-full', tint.dot)} aria-hidden />
          <span className="text-[15px] font-semibold text-[var(--color-foreground)]">
            {capitalize(state)}
          </span>
        </div>
        <p className="mb-4 text-[13px] text-[var(--color-foreground-muted)]">
          {describe(state)}
        </p>

        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-[13px]">
          <dt className="text-[var(--color-foreground-muted)]">PID</dt>
          <dd className="font-mono text-[var(--color-foreground)]">
            {status?.pid ?? '—'}
          </dd>
          <dt className="text-[var(--color-foreground-muted)]">Port</dt>
          <dd className="font-mono text-[var(--color-foreground)]">
            {status?.port ? `127.0.0.1:${status.port}` : '—'}
          </dd>
          <dt className="text-[var(--color-foreground-muted)]">Config hash</dt>
          <dd className="font-mono text-[12px] text-[var(--color-foreground)]">
            {status?.config_hash ?? '—'}
          </dd>
          <dt className="text-[var(--color-foreground-muted)]">Crashes (session)</dt>
          <dd className="font-mono text-[var(--color-foreground)]">
            {status?.crash_count ?? 0}
          </dd>
        </dl>

        {status?.last_error && (
          <div className="mt-4 flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/10 px-3 py-2">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-destructive)]" />
            <p className="text-[12px] font-mono text-[var(--color-destructive)]">
              {status.last_error}
            </p>
          </div>
        )}
      </div>

      {/* Apply */}
      <div className="mt-6">
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          Apply pending changes
        </h2>
        <p className="mb-3 text-[13px] text-[var(--color-foreground-muted)]">
          Respawns the subprocess with the current database state. Any in-flight requests are allowed to finish during a 30-second grace period.
        </p>
        <Button onClick={doApply} disabled={applying}>
          {applying ? (
            <>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              Applying…
            </>
          ) : (
            'Apply changes'
          )}
        </Button>
      </div>

      {/* Hard restart */}
      <div className="mt-8 border-t border-[var(--color-border)] pt-6">
        <h2 className="mb-2 text-[14px] font-semibold text-[var(--color-foreground)]">
          Hard restart
        </h2>
        <p className="mb-3 text-[13px] text-[var(--color-foreground-muted)]">
          Force a fresh spawn. Use this when the subprocess is in <code className="text-[12px]">FAILED</code>, or when recovering after an upstream outage.
        </p>
        <Button
          variant="outline"
          onClick={doRestart}
          disabled={restarting}
        >
          {restarting ? (
            <>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              Restarting…
            </>
          ) : (
            <>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              Hard restart
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

interface Tint { border: string; bg: string; dot: string }

function stateTint(state: string): Tint {
  switch (state) {
    case 'running':
      return { border: 'border-[var(--color-success)]/30', bg: 'bg-[var(--color-success)]/10', dot: 'bg-[var(--color-success)]/100' }
    case 'starting':
    case 'restarting':
      return { border: 'border-[var(--color-success-soft)]/30', bg: 'bg-[var(--color-success-soft)]/10', dot: 'bg-[var(--color-success-soft)]/100' }
    case 'crashed':
      return { border: 'border-[var(--color-warning)]/30', bg: 'bg-[var(--color-warning)]/10', dot: 'bg-[var(--color-warning)]/100' }
    case 'failed':
      return { border: 'border-[var(--color-destructive)]/30', bg: 'bg-[var(--color-destructive)]/10', dot: 'bg-[var(--color-destructive)]/100' }
    default:
      return { border: 'border-[var(--color-border)]', bg: 'bg-white', dot: 'bg-[rgba(0,0,0,0.25)]' }
  }
}

function describe(state: string): string {
  switch (state) {
    case 'running': return 'litellm subprocess is healthy and accepting requests.'
    case 'starting': return 'Waiting for the subprocess health probe to succeed.'
    case 'restarting': return 'Respawning with the current configuration.'
    case 'crashed': return 'Subprocess exited unexpectedly — auto-respawn in progress.'
    case 'failed': return 'Auto-respawn gave up. Use Hard restart to try again.'
    case 'terminated': return 'Server is shutting down.'
    case 'stopped': return 'Gracefully stopped; a respawn is imminent.'
    default: return 'Gateway is not enabled.'
  }
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}
