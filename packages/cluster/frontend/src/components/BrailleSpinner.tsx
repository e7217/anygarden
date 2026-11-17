import { useEffect, useState } from 'react'

// Low-key loading indicator used by the typing-in-progress bubble and,
// post #94, by question bubbles awaiting a room_query result. Ten
// frames rotated at 80 ms gives a readable but unhurried animation.
const BRAILLE_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
const FRAME_MS = 80
// DESIGN.md §7 — a busy signal keeps a slow turn under reduced motion
// rather than stopping.
const REDUCED_FRAME_MS = 400

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Decorative: every caller shows the busy state in text beside it, so
 * the glyph is hidden from assistive technology. The timer pauses while
 * the tab is hidden. */
export default function BrailleSpinner() {
  const [frame, setFrame] = useState(0)
  useEffect(() => {
    let id: number | undefined
    const stop = () => {
      if (id !== undefined) window.clearInterval(id)
      id = undefined
    }
    const start = () => {
      stop()
      if (document.visibilityState === 'hidden') return
      id = window.setInterval(
        () => setFrame(f => (f + 1) % BRAILLE_FRAMES.length),
        prefersReducedMotion() ? REDUCED_FRAME_MS : FRAME_MS,
      )
    }
    start()
    document.addEventListener('visibilitychange', start)
    const motion = typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null
    motion?.addEventListener?.('change', start)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', start)
      motion?.removeEventListener?.('change', start)
    }
  }, [])
  return (
    <span aria-hidden="true" className="text-caption text-[var(--color-foreground-muted)]">
      {BRAILLE_FRAMES[frame]}
    </span>
  )
}
