import { useEffect, useId, useRef, useState } from 'react'
import {
  BookOpen, Network, Package, Plug, Server, Settings, Share2, Waypoints,
} from 'lucide-react'

interface SidebarAdminMenuProps {
  pathname: string
  updateAvailable: boolean
  onGo: (path: string) => void
}

const links = [
  { label: 'Machines', path: '/admin/machines', icon: Server },
  { label: 'System', path: '/admin/system', icon: Package },
  { label: 'Skills', path: '/admin/skills', icon: BookOpen },
  { label: 'MCP Servers', path: '/admin/mcp-templates', icon: Plug },
  { label: 'Usage', path: '/admin/usage', icon: Waypoints, children: true },
  { label: 'Federation', path: '/admin/federation', icon: Network, children: true, experimental: true },
  { label: 'Topology', path: '/topology', icon: Share2, experimental: true },
]

function ExperimentalNavBadge() {
  return (
    <span
      aria-hidden="true"
      title="Experimental feature"
      className="ml-auto shrink-0 rounded-[var(--radius-pill)] border border-[var(--color-border-subtle)] bg-[var(--color-brand-tint-bg)] px-1.5 py-[1px] text-[10px] font-semibold leading-4 text-[var(--color-brand-tint-text)]"
    >
      Experimental
    </span>
  )
}

export default function SidebarAdminMenu({ pathname, updateAvailable, onGo }: SidebarAdminMenuProps) {
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const firstLinkRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    firstLinkRef.current?.focus()

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
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label={updateAvailable ? 'Admin settings, update available' : 'Admin settings'}
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="Admin settings"
        onClick={() => setOpen(value => !value)}
        className="relative flex min-h-11 min-w-11 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] transition-colors hover:bg-black/5 hover:text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
      >
        <Settings className="h-4 w-4" />
        {updateAvailable && (
          <span aria-hidden="true" className="absolute right-1 top-1 h-2.5 w-2.5 rounded-full border border-[var(--color-surface-alt)] bg-[#097fe8]" />
        )}
      </button>
      {open && (
        // Align past the 44px Logout button and 4px gap so the menu stays inside the sidebar.
        <div
          id={menuId}
          role="group"
          aria-label="Admin navigation"
          className="absolute bottom-full -right-12 z-50 mb-1 max-h-[calc(100dvh-5rem)] w-60 max-w-[calc(100vw-1rem)] overflow-y-auto overscroll-contain rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-1 shadow-lg"
        >
          {links.map((link, index) => {
            const Icon = link.icon
            const active = link.children
              ? pathname === link.path || pathname.startsWith(`${link.path}/`)
              : pathname === link.path
            return (
              <button
                key={link.path}
                ref={index === 0 ? firstLinkRef : undefined}
                type="button"
                aria-label={link.experimental ? `${link.label}, experimental feature` : undefined}
                aria-current={active ? 'page' : undefined}
                onClick={() => {
                  setOpen(false)
                  onGo(link.path)
                }}
                className={`flex min-h-11 w-full items-center rounded-[var(--radius-sm)] px-2 text-left text-sm font-medium transition-colors ${
                  active
                    ? 'bg-white shadow-whisper text-[var(--color-foreground)]'
                    : 'text-[var(--color-foreground-muted)] hover:bg-black/5 hover:text-[var(--color-foreground)]'
                }`}
              >
                <Icon className="mr-2 h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
                <span className="min-w-0 truncate">{link.label}</span>
                {link.label === 'System' && updateAvailable && (
                  <span className="ml-auto rounded-full bg-[#f2f9ff] px-2 py-0.5 text-[11px] font-semibold text-[#097fe8]" title="Update available">
                    update
                  </span>
                )}
                {link.experimental && <ExperimentalNavBadge />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
