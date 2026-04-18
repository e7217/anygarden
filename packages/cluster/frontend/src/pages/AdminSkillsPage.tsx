import { useState } from 'react'
import Sidebar from '@/components/Sidebar'
import SidebarExpandButton from '@/components/SidebarExpandButton'
import AdminSkills from '@/components/AdminSkills'
import { Button } from '@/components/ui/button'
import { Menu } from 'lucide-react'

export default function AdminSkillsPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-background)]">
      <Sidebar selectedRoom={null} open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <SidebarExpandButton />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--color-background)]">
        {/* Mobile top bar */}
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-[var(--color-border)] bg-white px-4 md:hidden">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
          <span className="text-[15px] font-bold tracking-tight">Skills</span>
        </div>
        <div className="flex-1 overflow-auto">
          <AdminSkills />
        </div>
      </main>
    </div>
  )
}
