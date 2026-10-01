import { Link, useLocation } from 'react-router-dom'
import { Compass } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'

/** #773 — catch-all route; an unknown URL used to render a blank page. */
export default function NotFoundPage() {
  const { t } = useLocale()
  const { pathname } = useLocation()
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-[var(--color-surface-alt)] px-6 text-center">
      <div className="flex w-full max-w-sm flex-col items-center">
        <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-[var(--radius-xl)] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-whisper">
          <Compass className="h-8 w-8 text-[var(--color-foreground-muted)]" aria-hidden="true" />
        </div>
        <h1 className="text-lead text-[var(--color-foreground)]">{t('common.notFoundTitle')}</h1>
        <p className="mt-2 text-sm text-[var(--color-foreground-muted)]">
          {t('common.notFoundDescription')}
        </p>
        <code className="mt-3 max-w-full break-all rounded-[var(--radius-sm)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-foreground-muted)]">
          {pathname}
        </code>
        <Button asChild className="mt-6 min-h-11">
          <Link to="/">{t('common.goHome')}</Link>
        </Button>
      </div>
    </main>
  )
}
