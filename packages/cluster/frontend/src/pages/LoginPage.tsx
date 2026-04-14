import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import LoginForm from '@/components/LoginForm'

export default function LoginPage() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()

  useEffect(() => {
    if (!loading && user) navigate('/', { replace: true })
  }, [user, loading, navigate])

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[var(--color-surface-alt)]">
        <p className="text-caption">Loading...</p>
      </div>
    )
  }

  return (
    <div className="flex h-screen items-center justify-center bg-[var(--color-surface-alt)] px-4">
      <LoginForm />
    </div>
  )
}
