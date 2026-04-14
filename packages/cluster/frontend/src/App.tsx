import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { RoomsProvider } from '@/hooks/useRooms'
import LoginPage from '@/pages/LoginPage'
import ChatPage from '@/pages/ChatPage'
import AdminMachinesPage from '@/pages/AdminMachinesPage'

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
          <Route path="/" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
          <Route path="/rooms/:roomId" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
          <Route path="/admin/agents" element={<Navigate to="/admin/machines" replace />} />
          <Route path="/admin/machines" element={<AdminRoute><AdminMachinesPage /></AdminRoute>} />
        </Routes>
      </RoomsProvider>
    </BrowserRouter>
  )
}
