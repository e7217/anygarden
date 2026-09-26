import { useEffect, useId, useRef, useState } from 'react'
import { ChevronUp, SlidersHorizontal } from 'lucide-react'
import { useLocale } from '@/i18n/LocaleProvider'
import { LocaleToggle } from '@/i18n/LocaleToggle'
import { ThemeToggle } from '@/theme/ThemeToggle'

interface SidebarPreferencesMenuProps {
  email?: string
  serverVersion: string | null
}

/** Account preferences share a single, stable home beneath navigation. */
export default function SidebarPreferencesMenu({ email, serverVersion }: SidebarPreferencesMenuProps) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    panelRef.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')?.focus()

    const onOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={rootRef} className="relative min-w-0 flex-1">
      <button
        ref={triggerRef}
        type="button"
        title={t('common.personalSettings')}
        aria-label={t('common.personalSettings')}
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(value => !value)}
        className="flex min-h-[var(--control-sm-height)] w-full min-w-0 items-center gap-2 rounded-[var(--radius-sm)] px-1 text-left text-[var(--color-foreground-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        <SlidersHorizontal className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-xs" title={email}>{email}</span>
          {serverVersion && (
            <span className="truncate text-xs text-[var(--color-foreground-subtle)]" title={t('chat.serverVersion')}>
              v{serverVersion}
            </span>
          )}
        </span>
        <ChevronUp className={`h-3 w-3 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <div
          ref={panelRef}
          id={menuId}
          role="group"
          data-drawer-popup
          aria-label={t('common.personalSettings')}
          className="absolute bottom-full left-0 z-50 mb-3 w-60 max-w-[calc(100vw-1.5rem)] rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface)] p-3 shadow-lg"
        >
          <p className="mb-3 text-sm font-semibold text-[var(--color-foreground)]">{t('common.personalSettings')}</p>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium text-[var(--color-foreground-muted)]">{t('common.language')}</span>
            <LocaleToggle />
          </div>
          <div className="mt-2 flex items-center justify-between gap-3 border-t border-[var(--color-border-subtle)] pt-2">
            <span className="text-xs font-medium text-[var(--color-foreground-muted)]">{t('common.theme')}</span>
            <ThemeToggle showLabel className="aria-pressed:bg-[var(--color-brand-tint-bg)] aria-pressed:text-[var(--color-brand-tint-text)]" />
          </div>
        </div>
      )}
    </div>
  )
}
