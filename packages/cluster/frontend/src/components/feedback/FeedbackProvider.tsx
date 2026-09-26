import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useLocale } from '@/i18n/LocaleProvider'

export interface ConfirmOptions {
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  destructive?: boolean
}

export interface NoticeOptions {
  message: string
  tone?: 'success' | 'error' | 'info'
}

interface FeedbackValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>
  notify: (options: NoticeOptions) => void
}

const FeedbackContext = createContext<FeedbackValue>({
  // Keeps isolated component previews functional. The application installs
  // FeedbackProvider in main.tsx and uses the in-app UI below.
  confirm: async ({ title, description }) => window.confirm(`${title}\n\n${description}`),
  notify: ({ message }) => window.alert(message),
})

interface Notice extends NoticeOptions { id: number }

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const { t } = useLocale()
  const [pending, setPending] = useState<ConfirmOptions | null>(null)
  const resolver = useRef<((result: boolean) => void) | null>(null)
  const [notices, setNotices] = useState<Notice[]>([])
  const nextId = useRef(0)
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])

  useEffect(() => () => {
    resolver.current?.(false)
    for (const timer of timers.current) clearTimeout(timer)
  }, [])

  const settle = useCallback((result: boolean) => {
    resolver.current?.(result)
    resolver.current = null
    setPending(null)
  }, [])

  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => {
    resolver.current?.(false)
    resolver.current = resolve
    setPending(options)
  }), [])

  const notify = useCallback(({ message, tone = 'info' }: NoticeOptions) => {
    const id = ++nextId.current
    setNotices((current) => [...current, { id, message, tone }])
    const timer = setTimeout(() => setNotices((current) => current.filter((item) => item.id !== id)), 5000)
    timers.current.push(timer)
  }, [])

  return (
    <FeedbackContext.Provider value={{ confirm, notify }}>
      {children}
      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) settle(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{pending?.title}</DialogTitle>
            <DialogDescription>{pending?.description}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => settle(false)}>{pending?.cancelLabel ?? t('common.cancel')}</Button>
            <Button variant={pending?.destructive ? 'destructive' : 'default'} onClick={() => settle(true)}>
              {pending?.confirmLabel ?? t('common.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {notices.length > 0 && (
        <div className="pointer-events-none fixed inset-x-4 bottom-4 z-[70] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-4 sm:w-96">
          {notices.map((notice) => (
            <div
              key={notice.id}
              role={notice.tone === 'error' ? 'alert' : 'status'}
              className={`pointer-events-auto flex w-full items-start gap-3 rounded-[var(--radius-lg)] border bg-[var(--color-surface-elevated)] p-3 text-sm text-[var(--color-foreground)] shadow-deep ${
                notice.tone === 'error' ? 'border-[var(--color-danger)]' : 'border-[var(--color-border)]'
              }`}
            >
              <span className="min-w-0 flex-1">{notice.message}</span>
              <button
                type="button"
                className="flex min-h-9 min-w-9 items-center justify-center rounded-[var(--radius-sm)] text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-hover)]"
                onClick={() => setNotices((current) => current.filter((item) => item.id !== notice.id))}
                aria-label={t('common.dismiss')}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </FeedbackContext.Provider>
  )
}

export function useFeedback(): FeedbackValue {
  return useContext(FeedbackContext)
}
