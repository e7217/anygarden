import { useCallback, useEffect, useState } from 'react'

export type ThreadDisplayMode = 'panel' | 'inline'

const STORAGE_KEY = 'anygarden.threadDisplayMode'
const DEFAULT_MODE: ThreadDisplayMode = 'panel'
/** Fired on change so every mounted reader stays in sync in one tab. */
const CHANGE_EVENT = 'anygarden:thread-display-mode'

function isMode(value: unknown): value is ThreadDisplayMode {
  return value === 'panel' || value === 'inline'
}

function read(): ThreadDisplayMode {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isMode(stored) ? stored : DEFAULT_MODE
  } catch {
    // Private-mode / disabled storage — fall back rather than crash the
    // room. The preference simply doesn't persist.
    return DEFAULT_MODE
  }
}

/**
 * Where a thread's replies are rendered: in the right-hand panel, or
 * expanded inline underneath the message.
 *
 * Both surfaces exist while the shape is being settled — they read the
 * same grouped data (``lib/threads.ts``) and post through the same
 * WebSocket path, so switching changes only presentation. Once one
 * wins, delete the other and this hook with it.
 */
export function useThreadDisplayMode(): [
  ThreadDisplayMode,
  (mode: ThreadDisplayMode) => void,
] {
  const [mode, setModeState] = useState<ThreadDisplayMode>(read)

  useEffect(() => {
    const onChange = () => setModeState(read())
    window.addEventListener(CHANGE_EVENT, onChange)
    // ``storage`` covers the same preference changed in another tab.
    window.addEventListener('storage', onChange)
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange)
      window.removeEventListener('storage', onChange)
    }
  }, [])

  const setMode = useCallback((next: ThreadDisplayMode) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Non-persisting is acceptable; the in-memory switch still works.
    }
    setModeState(next)
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT))
  }, [])

  return [mode, setMode]
}
