import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

interface SidebarMenuPopoverProps {
  anchorRef: RefObject<HTMLDivElement | null>
  label: string
  onClose: () => void
  children: ReactNode
  width?: 'normal' | 'narrow'
}

/** Row menus escape the navigation scroll viewport and flip above low anchors. */
export default function SidebarMenuPopover({ anchorRef, label, onClose, children, width = 'normal' }: SidebarMenuPopoverProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: 8, top: 8 })
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useLayoutEffect(() => {
    const drawer = anchorRef.current?.closest<HTMLElement>('[data-navigation-drawer]')
    if (drawer && menuRef.current) menuRef.current.dataset.drawerOwner = drawer.id
    const updatePosition = () => {
      const anchorElement = anchorRef.current
      if (anchorElement?.closest('[inert], [aria-hidden="true"]') || (anchorElement && getComputedStyle(anchorElement).visibility === 'hidden')) {
        closeRef.current()
        return
      }
      const anchor = anchorRef.current?.getBoundingClientRect()
      const menu = menuRef.current?.getBoundingClientRect()
      if (!anchor || !menu) return
      const viewport = anchorElement?.closest('[data-radix-scroll-area-viewport]')?.getBoundingClientRect()
      if (viewport && viewport.height > 0 && (anchor.bottom <= viewport.top || anchor.top >= viewport.bottom)) {
        closeRef.current()
        return
      }
      const margin = 8
      const below = anchor.bottom + 4
      const top = below + menu.height <= window.innerHeight - margin
        ? below
        : Math.max(margin, anchor.top - menu.height - 4)
      const left = Math.max(margin, Math.min(anchor.right - menu.width, window.innerWidth - menu.width - margin))
      setPosition({ left, top })
    }
    const onOutside = (event: PointerEvent) => {
      const target = event.target as Node
      if (!anchorRef.current?.contains(target) && !menuRef.current?.contains(target)) closeRef.current()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      closeRef.current()
      anchorRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    }
    const visibilityObserver = new MutationObserver(updatePosition)
    if (drawer) visibilityObserver.observe(drawer, { attributes: true, attributeFilter: ['aria-hidden', 'inert', 'class'] })
    updatePosition()
    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    document.addEventListener('pointerdown', onOutside)
    document.addEventListener('keydown', onKey)
    return () => {
      visibilityObserver.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
      document.removeEventListener('pointerdown', onOutside)
      document.removeEventListener('keydown', onKey)
    }
  }, [anchorRef])

  return createPortal(
    <div
      ref={menuRef}
      role="group"
      aria-label={label}
      style={position}
      className={`fixed z-[60] max-h-[calc(100dvh-1rem)] max-w-[calc(100vw-1rem)] overflow-y-auto overscroll-contain rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-lg ${width === 'narrow' ? 'w-40' : 'w-52'}`}
    >
      {children}
    </div>,
    document.body,
  )
}
