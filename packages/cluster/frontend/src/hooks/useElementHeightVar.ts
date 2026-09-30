import { useEffect } from 'react'

/**
 * Publish ``source``'s border-box height as a CSS custom property on
 * ``target`` (#760). The right-hand slot's header reads it so its
 * bottom border lands on the room header's, which changes height when
 * the chat column narrows enough to wrap it onto two rows.
 *
 * Written straight to the DOM rather than through state: the value
 * only feeds CSS, and a re-render per resize would repaint the whole
 * chat column for nothing.
 */
export function useElementHeightVar(
  source: Element | null,
  target: HTMLElement | null,
  name: string,
): void {
  useEffect(() => {
    if (!source || !target || typeof ResizeObserver === 'undefined') return
    let last: number | null = null
    const observer = new ResizeObserver(entries => {
      const entry = entries[entries.length - 1]
      const height =
        entry?.borderBoxSize?.[0]?.blockSize ?? source.getBoundingClientRect().height
      if (height === last) return
      last = height
      target.style.setProperty(name, `${height}px`)
    })
    observer.observe(source)
    return () => {
      observer.disconnect()
      target.style.removeProperty(name)
    }
  }, [source, target, name])
}
