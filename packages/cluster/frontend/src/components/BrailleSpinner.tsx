import { useEffect, useState } from 'react'

// Low-key loading indicator used by the typing-in-progress bubble and,
// post #94, by question bubbles awaiting a room_query result. Ten
// frames rotated at 80 ms gives a readable but unhurried animation.
const BRAILLE_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

export default function BrailleSpinner() {
  const [frame, setFrame] = useState(0)
  useEffect(() => {
    const id = setInterval(
      () => setFrame(f => (f + 1) % BRAILLE_FRAMES.length),
      80,
    )
    return () => clearInterval(id)
  }, [])
  return (
    <span className="text-sm text-[var(--color-foreground-muted)]">
      {BRAILLE_FRAMES[frame]}
    </span>
  )
}
