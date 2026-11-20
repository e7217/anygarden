import { useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { apiFetch } from '@/lib/api'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'

export default function LoginForm() {
  const { login, register } = useAuth()
  const { t } = useLocale()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('login')
  const [registrationOpen, setRegistrationOpen] = useState(true)

  // #902 — a fresh node has no account to log in to; open on Create account.
  // #1058 — once an admin closes sign-up, only Log in remains.
  useEffect(() => {
    let active = true
    apiFetch('/api/v1/auth/setup')
      .then(async response => {
        if (!response.ok) return
        const body = await response.json() as { needs_first_account?: boolean; registration_open?: boolean }
        if (!active) return
        if (body.needs_first_account) setTab('register')
        else if (body.registration_open === false) setRegistrationOpen(false)
      })
      .catch(() => {})
    return () => { active = false }
  }, [])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      window.location.href = '/'
    } catch (err) {
      setError(err instanceof Error && err.message === 'Account disabled'
        ? t('auth.accountDisabled') : t('auth.loginFailed'))
    }
    setLoading(false)
  }

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await register(email, password)
      window.location.href = '/'
    } catch (err) {
      const message = err instanceof Error ? err.message : ''
      if (message === 'Registration is closed') {
        setRegistrationOpen(false)
        setTab('login')
        setError(t('auth.registrationClosed'))
      } else {
        setError(message === 'Email already registered' ? t('auth.emailRegistered') : t('auth.registerFailed'))
      }
    }
    setLoading(false)
  }

  return (
    <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-card sm:p-8">
      <div className="mb-6 text-center">
        <h1 className="text-title mb-1 text-[var(--color-foreground)]">Anygarden</h1>
        <p className="text-caption text-[var(--color-foreground-muted)]">{t('auth.tagline')}</p>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        {registrationOpen && (
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="login">{t('auth.login')}</TabsTrigger>
            <TabsTrigger value="register">{t('auth.register')}</TabsTrigger>
          </TabsList>
        )}

        {error && (
          <div role="alert" className="mt-4 rounded-[var(--radius-md)] border border-[var(--color-danger)]/20 bg-[var(--color-danger)]/10 p-3 text-caption text-[var(--color-danger)]">
            {error}
          </div>
        )}

        <TabsContent value="login">
          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="login-email">{t('auth.email')}</Label>
              <Input
                id="login-email"
                type="email"
                placeholder={t('auth.emailPlaceholder')}
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="login-password">{t('auth.password')}</Label>
              <Input
                id="login-password"
                type="password"
                placeholder={t('auth.passwordPlaceholder')}
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? t('auth.signingIn') : t('auth.signIn')}
            </Button>
            {!registrationOpen && (
              <p className="text-center text-caption text-[var(--color-foreground-muted)]">{t('auth.registrationClosed')}</p>
            )}
          </form>
        </TabsContent>

        <TabsContent value="register">
          <form onSubmit={handleRegister} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="reg-email">{t('auth.email')}</Label>
              <Input
                id="reg-email"
                type="email"
                placeholder={t('auth.emailPlaceholder')}
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="reg-password">{t('auth.password')}</Label>
              <Input
                id="reg-password"
                type="password"
                placeholder={t('auth.newPasswordPlaceholder')}
                value={password}
                onChange={e => setPassword(e.target.value)}
                required
                minLength={8}
                aria-describedby="reg-password-hint"
              />
              <p id="reg-password-hint" className="text-caption text-[var(--color-foreground-muted)]">{t('auth.passwordHint')}</p>
            </div>
            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? t('auth.creatingAccount') : t('auth.register')}
            </Button>
          </form>
        </TabsContent>
      </Tabs>
    </div>
  )
}
