import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import LoginForm from '@/components/LoginForm'
import { LocaleToggle } from '@/i18n/LocaleToggle'
import { ThemeToggle } from '@/theme/ThemeToggle'
import { useLocale } from '@/i18n/LocaleProvider'

export default function LoginPage() {
  const { user, loading } = useAuth()
  const { t } = useLocale()
  const navigate = useNavigate()

  useEffect(() => {
    if (!loading && user) navigate('/', { replace: true })
  }, [user, loading, navigate])

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-[var(--color-surface-alt)]">
        <p className="text-caption text-[var(--color-foreground-muted)]">{t('common.loading')}</p>
      </div>
    )
  }

  return (
    <div className="relative flex min-h-dvh items-center justify-center bg-[var(--color-surface-alt)] px-4 py-16">
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LocaleToggle compact />
        <ThemeToggle />
      </div>
      <LoginForm />
    </div>
  )
}
