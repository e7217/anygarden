import { Suspense, lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { RoomsProvider } from '@/hooks/useRooms'
import LoginPage from '@/pages/LoginPage'
import ChatPage from '@/pages/ChatPage'
import AdminMachinesPage from '@/pages/AdminMachinesPage'
import GuestInvitePage from '@/pages/GuestInvitePage'
import GuestRoomPage from '@/pages/GuestRoomPage'

// Topology view is code-split. Pulls in @xyflow/react + dagre
// (~110KB gzip combined) only when the route is actually visited.
const TopologyPage = lazy(() => import('@/pages/TopologyPage'))

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="flex items-center justify-center h-screen">Loading...</div>
  if (!user) return <Navigate to="/login" />
  return <>{children}</>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="flex items-center justify-center h-screen">Loading...</div>
  if (!user) return <Navigate to="/login" />
  if (!user.is_admin) return <Navigate to="/" />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      {/* RoomsProvider hosts the single projects/rooms store so
          the Sidebar and ChatPage share state — when one triggers
          a refetch (e.g. after creating a sub-room) every other
          subscriber sees the new tree immediately, instead of
          going stale until the user reloads. */}
      <RoomsProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* Guest entry + single-room shell. Intentionally NOT
              wrapped in ProtectedRoute — the guest flow has its own
              JWT lifecycle and must not redirect through /login. */}
          <Route path="/invite/:token" element={<GuestInvitePage />} />
          <Route path="/g/:roomId" element={<GuestRoomPage />} />
          <Route path="/" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
          <Route path="/rooms/:roomId" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
          <Route path="/admin/agents" element={<Navigate to="/admin/machines" replace />} />
          <Route path="/admin/machines" element={<AdminRoute><AdminMachinesPage /></AdminRoute>} />
          <Route
            path="/topology"
            element={
              <ProtectedRoute>
                <Suspense
                  fallback={
                    <div className="flex items-center justify-center h-screen text-[var(--color-foreground-muted)]">
                      Loading topology…
                    </div>
                  }
                >
                  <TopologyPage />
                </Suspense>
              </ProtectedRoute>
            }
          />
        </Routes>
      </RoomsProvider>
    </BrowserRouter>
  )
}
