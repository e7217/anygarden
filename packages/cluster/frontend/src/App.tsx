import { Suspense, lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { RoomsProvider } from '@/hooks/useRooms'
import { SidebarLayoutProvider } from '@/hooks/useSidebarLayout'
import { RightSidebarLayoutProvider } from '@/hooks/useRightSidebarLayout'
import LoginPage from '@/pages/LoginPage'
import ChatPage from '@/pages/ChatPage'
import GuestInvitePage from '@/pages/GuestInvitePage'
import GuestRoomPage from '@/pages/GuestRoomPage'
import FederationPreviewPage from '@/pages/FederationPreviewPage'
import { useLocale } from '@/i18n/LocaleProvider'

// Topology view is code-split. Pulls in @xyflow/react + dagre
// (~110KB gzip combined) only when the route is actually visited.
const TopologyPage = lazy(() => import('@/pages/TopologyPage'))

// #651 — admin surfaces are code-split for the same reason as Topology,
// with a stronger case behind it: every one of these sits under
// <AdminRoute>, so a non-admin can never render them, yet before this
// they shipped inside the entry chunk that every visitor downloads
// before the login form paints.
const AdminMachinesPage = lazy(() => import('@/pages/AdminMachinesPage'))
const AdminSkillsPage = lazy(() => import('@/pages/AdminSkillsPage'))
const AdminSystemPage = lazy(() => import('@/pages/AdminSystemPage'))
const AdminMCPTemplatesPage = lazy(() => import('@/pages/AdminMCPTemplatesPage'))
const AdminUsagePage = lazy(() => import('@/pages/AdminUsagePage'))
const AdminFederationPage = lazy(() => import('@/pages/AdminFederationPage'))

/** Full-viewport placeholder, matching the route-level loading states. */
function RouteFallback({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-screen text-[var(--color-foreground-muted)]">
      {label}
    </div>
  )
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const { t } = useLocale()
  if (loading) return <div className="flex items-center justify-center h-screen">{t('common.loading')}</div>
  if (!user) return <Navigate to="/login" />
  return <>{children}</>
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const { t } = useLocale()
  if (loading) return <div className="flex items-center justify-center h-screen">{t('common.loading')}</div>
  if (!user) return <Navigate to="/login" />
  if (!user.is_admin) return <Navigate to="/" />
  // Boundary lives after the auth gates so a non-admin is redirected
  // without ever requesting an admin chunk.
  return <Suspense fallback={<RouteFallback label={t('common.loading')} />}>{children}</Suspense>
}

export default function App() {
  const { t } = useLocale()
  // #593 — keep the fixture-only preview outside all product providers so
  // opening it cannot trigger room fetches, auth checks, or WebSockets.
  if (import.meta.env.DEV && window.location.pathname === '/__preview/federation') {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="/__preview/federation" element={<FederationPreviewPage />} />
        </Routes>
      </BrowserRouter>
    )
  }

  return (
    <BrowserRouter>
      {/* RoomsProvider hosts the single projects/rooms store so
          the Sidebar and ChatPage share state — when one triggers
          a refetch (e.g. after creating a sub-room) every other
          subscriber sees the new tree immediately, instead of
          going stale until the user reloads. */}
      <RoomsProvider>
        {/* #115 — SidebarLayoutProvider holds the desktop collapsed
            flag + its localStorage-backed persistence + Ctrl/Cmd+B
            handler (mounted inside <Sidebar>). Sitting under
            RoomsProvider keeps the reading order "data → layout",
            and is still safe on routes without a sidebar (LoginPage,
            guest pages) because the provider has zero side effects
            until a consumer mounts. */}
        <SidebarLayoutProvider>
          {/* #302 — Right context rail collapse state. Sibling to the
              left SidebarLayoutProvider; default *closed* so the chat
              canvas wins width until the user opts in. */}
          <RightSidebarLayoutProvider>
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
            <Route path="/admin/system" element={<AdminRoute><AdminSystemPage /></AdminRoute>} />
            <Route path="/admin/skills" element={<AdminRoute><AdminSkillsPage /></AdminRoute>} />
            <Route path="/admin/mcp-templates" element={<AdminRoute><AdminMCPTemplatesPage /></AdminRoute>} />
            {/* #593 — federated collaboration admin. AdminRoute gates the
                node/peer and shared-channel surfaces, which are all
                admin-scoped on the backend. */}
            <Route path="/admin/federation" element={<AdminRoute><AdminFederationPage /></AdminRoute>} />
            <Route path="/admin/usage" element={<AdminRoute><AdminUsagePage /></AdminRoute>} />
            <Route path="/admin/llm-gateway/*" element={<AdminRoute><Navigate to="/admin/usage" replace /></AdminRoute>} />
            <Route
              path="/topology"
              element={
                <ProtectedRoute>
                  <Suspense fallback={<RouteFallback label={t('navigation.loadingTopology')} />}>
                    <TopologyPage />
                  </Suspense>
                </ProtectedRoute>
              }
            />
            </Routes>
          </RightSidebarLayoutProvider>
        </SidebarLayoutProvider>
      </RoomsProvider>
    </BrowserRouter>
  )
}
