import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, X } from 'lucide-react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { DialogOverlay, DialogPortal, DialogTitle } from '@/components/ui/dialog'
import { apiFetch } from '@/lib/api'
import { formatMessageTimestamp } from '@/lib/datetime'
import { useLocale } from '@/i18n/LocaleProvider'

interface SearchResult {
  message_id: string
  room_id: string
  participant_id: string | null
  content: string
  created_at: string
  snippet: string
}

interface SearchDialogProps {
  open: boolean
  onClose: () => void
  projectId?: string
}

export default function SearchDialog({ open, onClose, projectId }: SearchDialogProps) {
  const { locale, t } = useLocale()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const search = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return }
    setLoading(true)
    try {
      const params = new URLSearchParams({ q })
      if (projectId) params.set('project_id', projectId)
      const resp = await apiFetch(`/api/v1/search?${params}`)
      if (resp.ok) setResults(await resp.json())
      else setResults([])
    } catch {
      setResults([])
    }
    setLoading(false)
  }, [projectId])

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300)
    return () => clearTimeout(timer)
  }, [query, search])

  useEffect(() => {
    if (!open) { setQuery(''); setResults([]) }
  }, [open])

  return (
    <DialogPrimitive.Root open={open} onOpenChange={next => { if (!next) onClose() }}>
      <DialogPortal>
        <DialogOverlay />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[15vh] z-50 w-[calc(100%-1.5rem)] max-w-lg -translate-x-1/2 rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-[var(--color-surface)] shadow-deep focus:outline-none"
        >
        <DialogTitle className="sr-only">{t('chat.searchMessages')}</DialogTitle>
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3 transition-colors has-[input:focus-visible]:border-[var(--color-brand-focus)]">
          <Search aria-hidden="true" className="h-4 w-4 shrink-0 text-[var(--color-foreground-subtle)]" />
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('chat.searchPlaceholder')}
            aria-label={t('chat.searchMessages')}
            className="min-w-0 flex-1 bg-transparent px-2 py-1 text-base placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none md:text-sm"
          />
          <DialogPrimitive.Close aria-label={t('chat.closeSearch')} className="flex h-11 w-11 shrink-0 items-center justify-center rounded hover:bg-[var(--color-surface-hover)]">
            <X aria-hidden="true" className="h-4 w-4" />
          </DialogPrimitive.Close>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {loading && (
            <div className="px-4 py-3 text-sm text-[var(--color-foreground-muted)]">{t('chat.searching')}</div>
          )}
          {!loading && query && results.length === 0 && (
            <div className="px-4 py-3 text-sm text-[var(--color-foreground-muted)]">{t('chat.noResults')}</div>
          )}
          {results.map(r => (
            <button
              key={r.message_id}
              className="min-h-11 w-full px-4 py-2.5 text-left hover:bg-[var(--color-surface-alt)] transition-colors"
              onClick={() => {
                navigate(`/rooms/${r.room_id}`)
                onClose()
              }}
            >
              <div
                className="text-sm line-clamp-2 [&_mark]:rounded-[2px] [&_mark]:bg-[var(--color-brand-tint-bg)] [&_mark]:px-0.5 [&_mark]:text-[var(--color-brand-tint-text)]"
                dangerouslySetInnerHTML={{ __html: r.snippet }}
              />
              <div className="text-badge font-normal text-[var(--color-foreground-subtle)] mt-0.5">
                {/* #514 — share the chat bubble's #93-safe formatter: today
                    shows the time, older results are prefixed with the date. */}
                {formatMessageTimestamp(r.created_at, new Date(), locale)}
              </div>
            </button>
          ))}
        </div>
        </DialogPrimitive.Content>
      </DialogPortal>
    </DialogPrimitive.Root>
  )
}
