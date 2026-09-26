import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

const focusableSelector = 'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

/** Keep the existing rail mounted while its mobile overlay behaves as a modal. */
export function useModalDrawer({ open, onClose, panelRef, desktopMinWidth }: {
  open: boolean
  onClose?: () => void
  panelRef: RefObject<HTMLElement | null>
  desktopMinWidth: number
}) {
  const query = `(min-width: ${desktopMinWidth}px)`
  const [desktop, setDesktop] = useState(() => window.matchMedia?.(query).matches ?? window.innerWidth >= desktopMinWidth)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  const active = open && !desktop

  useEffect(() => {
    const media = window.matchMedia?.(query)
    const update = () => setDesktop(media?.matches ?? window.innerWidth >= desktopMinWidth)
    if (media) media.addEventListener('change', update)
    else window.addEventListener('resize', update)
    return () => {
      if (media) media.removeEventListener('change', update)
      else window.removeEventListener('resize', update)
    }
  }, [query, desktopMinWidth])

  useLayoutEffect(() => {
    const panel = panelRef.current
    if (!active || !panel) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const inertTargets: Array<{ element: HTMLElement; wasInert: boolean }> = []

    // Disable sibling branches, not a containing app root. Body portals opened
    // later by nested Radix dialogs remain available to their own focus scope.
    let branch: HTMLElement = panel
    while (branch.parentElement) {
      for (const sibling of branch.parentElement.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch
          || sibling.dataset.drawerOverlay === panel.id
          || sibling.dataset.drawerOwner === panel.id
          || ['SCRIPT', 'STYLE', 'LINK'].includes(sibling.tagName)) continue
        inertTargets.push({ element: sibling, wasInert: sibling.hasAttribute('inert') })
        sibling.setAttribute('inert', '')
      }
      branch = branch.parentElement
      if (branch === document.body) break
    }

    const scopes = () => [panel, ...document.querySelectorAll<HTMLElement>(`[data-drawer-owner="${panel.id}"]`)]
    const nestedDialogOpen = () => [...document.querySelectorAll<HTMLElement>('[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"]')]
      .some(dialog => !panel.contains(dialog))
    const focusable = () => scopes().flatMap(scope => [...scope.querySelectorAll<HTMLElement>(focusableSelector)])
      .filter(element => !element.closest('[inert], [hidden], [aria-hidden="true"]')
        && getComputedStyle(element).visibility !== 'hidden' && getComputedStyle(element).display !== 'none')
    const initialFocus = () => panel.querySelector<HTMLElement>('[data-drawer-close]') ?? focusable()[0] ?? panel
    initialFocus().focus({ preventScroll: true })

    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || nestedDialogOpen()) return
      if (event.key === 'Escape') {
        // A row menu gets the first Escape; the next closes its drawer.
        if (panel.querySelector('[data-drawer-popup]') || document.querySelector(`[data-drawer-owner="${panel.id}"]`)) return
        event.preventDefault()
        event.stopPropagation()
        closeRef.current?.()
      }
      if (event.key !== 'Tab') return
      const controls = focusable()
      const current = controls.indexOf(document.activeElement as HTMLElement)
      if (!controls.length) { event.preventDefault(); panel.focus(); return }
      if (event.shiftKey ? current <= 0 : current === -1 || current === controls.length - 1) {
        event.preventDefault()
        controls[event.shiftKey ? controls.length - 1 : 0].focus()
      }
    }
    const onFocus = (event: FocusEvent) => {
      if (nestedDialogOpen() || scopes().some(scope => scope.contains(event.target as Node))) return
      initialFocus().focus({ preventScroll: true })
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('focusin', onFocus)
      for (const { element, wasInert } of inertTargets) if (!wasInert) element.removeAttribute('inert')
      // Restore after React commits the hidden rail; its focus restoration
      // would otherwise put focus back into the just-closed drawer.
      queueMicrotask(() => {
        if (previousFocus?.isConnected && !previousFocus.closest('[inert]')
          && getComputedStyle(previousFocus).display !== 'none' && getComputedStyle(previousFocus).visibility !== 'hidden') {
          previousFocus.focus({ preventScroll: true })
        }
      })
    }
  }, [active, panelRef])

  return { desktop, active }
}
