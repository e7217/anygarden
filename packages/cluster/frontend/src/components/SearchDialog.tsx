import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search, X } from 'lucide-react'
import { apiFetch } from '@/lib/api'

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

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]" onClick={onClose}>
      <div className="fixed inset-0 bg-black/20" />
      <div
        className="relative w-full max-w-lg rounded-[var(--radius-lg)] border border-[var(--color-border)] bg-white shadow-lg"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
          <Search className="h-4 w-4 text-[var(--color-foreground-subtle)]" />
          <input
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search messages..."
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-foreground-subtle)]"
          />
          <button onClick={onClose} className="rounded p-1 hover:bg-black/5">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {loading && (
            <div className="px-4 py-3 text-sm text-[var(--color-foreground-muted)]">Searching...</div>
          )}
          {!loading && query && results.length === 0 && (
            <div className="px-4 py-3 text-sm text-[var(--color-foreground-muted)]">No results found</div>
          )}
          {results.map(r => (
            <button
              key={r.message_id}
              className="w-full px-4 py-2.5 text-left hover:bg-[var(--color-surface-alt)] transition-colors"
              onClick={() => {
                navigate(`/rooms/${r.room_id}`)
                onClose()
              }}
            >
              <div
                className="text-sm line-clamp-2"
                dangerouslySetInnerHTML={{ __html: r.snippet }}
              />
              <div className="text-[11px] text-[var(--color-foreground-subtle)] mt-0.5">
                {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
