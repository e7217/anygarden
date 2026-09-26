import { useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
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

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(email, password)
      window.location.href = '/'
    } catch {
      setError(t('auth.loginFailed'))
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
      setError(err instanceof Error && err.message === 'Email already registered'
        ? t('auth.emailRegistered') : t('auth.registerFailed'))
    }
    setLoading(false)
  }

  return (
    <div className="w-full max-w-sm rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] p-6 shadow-card sm:p-8">
      <div className="mb-6 text-center">
        <h1 className="text-title mb-1 text-[var(--color-foreground)]">Anygarden</h1>
        <p className="text-sm text-[var(--color-foreground-muted)]">{t('auth.tagline')}</p>
      </div>

      <Tabs defaultValue="login">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="login">{t('auth.login')}</TabsTrigger>
          <TabsTrigger value="register">{t('auth.register')}</TabsTrigger>
        </TabsList>

        {error && (
          <div role="alert" className="mt-4 rounded-[var(--radius-md)] border border-[var(--color-danger)]/20 bg-[var(--color-danger)]/10 p-3 text-sm text-[var(--color-danger)]">
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
                minLength={6}
              />
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
