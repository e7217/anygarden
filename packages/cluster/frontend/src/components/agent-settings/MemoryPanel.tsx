import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { apiFetch } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useLocale } from '@/i18n/LocaleProvider'

interface RoomChoice { id: string; name: string; project: string }
interface RoomMemory {
  agent_id: string
  room_id: string
  memory_md: string
  revision: number
  session_epoch: number
  ephemeral: boolean
  room_name: string
}

export default function MemoryPanel({ agentId, active }: { agentId: string; active: boolean }) {
  const { t } = useLocale()
  const location = useLocation()
  const initialRoom = useRef(location.pathname.match(/^\/rooms\/([^/]+)$/)?.[1] ?? '')
  const [rooms, setRooms] = useState<RoomChoice[]>([])
  const [roomId, setRoomId] = useState('')
  const [snapshot, setSnapshot] = useState<RoomMemory | null>(null)
  const [draft, setDraft] = useState('')
  const [legacy, setLegacy] = useState('')
  const [legacyDraft, setLegacyDraft] = useState('')
  const [editingLegacy, setEditingLegacy] = useState(false)
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogReload, setCatalogReload] = useState(0)
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const scope = useRef({ agentId, roomId, active, sequence: 0 })
  scope.current.agentId = agentId
  scope.current.roomId = roomId
  scope.current.active = active
  const dirty = snapshot !== null && draft !== snapshot.memory_md
  const loading = catalogLoading || memoryLoading

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    const accepts = () => !controller.signal.aborted && scope.current.agentId === agentId && scope.current.active
    async function read<T>(path: string): Promise<T> {
      const response = await apiFetch(path, { signal: controller.signal })
      if (!response.ok) throw new Error(String(response.status))
      return response.json() as Promise<T>
    }
    void (async () => {
      setCatalogLoading(true)
      try {
        const [assigned, projects, agent] = await Promise.all([
          read<Array<{ room_id: string; room_name: string }>>(`/api/v1/agents/${agentId}/rooms`),
          read<Array<{ id: string; name: string }>>('/api/v1/projects'),
          read<{ memory_md?: string | null }>(`/api/v1/agents/${agentId}`),
        ])
        const projectRooms = (await Promise.all(projects.map(async project => {
          const children = await read<Array<{ id: string; name: string }>>(`/api/v1/rooms?project_id=${project.id}&is_dm=false`)
          return children.map(room => ({ ...room, project: project.name }))
        }))).flat()
        const unique = new Map(assigned.map(room => [room.room_id, {
          id: room.room_id, name: room.room_name,
          project: projectRooms.find(item => item.id === room.room_id)?.project ?? t('agentMemory.privateConversation'),
        }]))
        if (!accepts()) return
        setRooms([...unique.values()])
        setLegacy(agent.memory_md ?? '')
        setLegacyDraft(agent.memory_md ?? '')
        setRoomId(previous => unique.has(previous) ? previous : (unique.has(initialRoom.current) ? initialRoom.current : ''))
        setError(null)
      } catch {
        if (accepts()) setError(t('agentMemory.loadFailed'))
      } finally {
        if (accepts()) setCatalogLoading(false)
      }
    })()
    return () => controller.abort()
  }, [agentId, active, catalogReload, t])

  const loadMemory = useCallback(async () => {
    if (!roomId || !active) return
    const sequence = ++scope.current.sequence
    const accepts = () => scope.current.agentId === agentId && scope.current.roomId === roomId && scope.current.active && scope.current.sequence === sequence
    setMemoryLoading(true)
    setError(null)
    setNotice(null)
    try {
      const response = await apiFetch(`/api/v1/agents/${agentId}/rooms/${roomId}/memory`)
      if (!response.ok) throw new Error(String(response.status))
      const next: RoomMemory = await response.json()
      if (accepts()) { setSnapshot(next); setDraft(next.memory_md) }
    } catch {
      if (accepts()) setError(t('agentMemory.loadFailed'))
    } finally {
      if (accepts()) setMemoryLoading(false)
    }
  }, [agentId, roomId, active, t])

  useEffect(() => {
    setBusy(false)
    setMemoryLoading(false)
    setSnapshot(null)
    setDraft('')
    void loadMemory()
    return () => { ++scope.current.sequence }
  }, [loadMemory])

  async function saveMemory(action: 'save' | 'clear' | 'import-legacy') {
    if (!snapshot || busy || loading) return
    const savedRoom = roomId
    const sequence = ++scope.current.sequence
    const accepts = () => scope.current.agentId === agentId && scope.current.roomId === savedRoom && scope.current.active && scope.current.sequence === sequence
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const base = `/api/v1/agents/${agentId}/rooms/${savedRoom}/memory`
      const response = await apiFetch(action === 'save' ? base : `${base}/${action}`, {
        method: action === 'save' ? 'PUT' : 'POST',
        body: JSON.stringify({ expected_revision: snapshot.revision, ...(action === 'save' ? { memory_md: draft } : {}) }),
      })
      if (!accepts()) return
      if (!response.ok) { setError(t(response.status === 409 ? 'agentMemory.conflict' : 'agentMemory.saveFailed')); return }
      const next: RoomMemory = await response.json()
      if (accepts()) { setSnapshot(next); setDraft(next.memory_md); setNotice(t('agentMemory.saved')) }
    } catch {
      if (accepts()) setError(t('agentMemory.saveFailed'))
    } finally {
      if (accepts()) setBusy(false)
    }
  }

  async function saveLegacy() {
    if (busy || loading) return
    setBusy(true)
    setError(null)
    const sequence = ++scope.current.sequence
    const accepts = () => scope.current.agentId === agentId && scope.current.active && scope.current.sequence === sequence
    try {
      const response = await apiFetch(`/api/v1/agents/${agentId}`, {
        method: 'PUT', body: JSON.stringify({ memory_md: legacyDraft || null, memory_md_set: true }),
      })
      if (!accepts()) return
      if (!response.ok) throw new Error(String(response.status))
      const next: { memory_md?: string | null } = await response.json()
      if (accepts()) { setLegacy(next.memory_md ?? ''); setLegacyDraft(next.memory_md ?? ''); setEditingLegacy(false); setNotice(t('agentMemory.archiveSaved')) }
    } catch {
      if (accepts()) setError(t('agentMemory.saveFailed'))
    } finally {
      if (accepts()) setBusy(false)
    }
  }

  function exportLegacy() {
    const url = URL.createObjectURL(new Blob([legacy], { type: 'text/markdown;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${agentId}-legacy-notes.md`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div className="space-y-3 min-w-0" data-testid="agent-memory-panel">
    <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentMemory.hint')}</p>
    <label className="block space-y-1 text-sm">
      <span>{t('agentMemory.room')}</span>
      <Select value={roomId} disabled={busy || loading || dirty} className="min-h-11" onChange={event => setRoomId(event.target.value)}>
        <option value="">{t('agentMemory.selectRoom')}</option>
        {rooms.map(room => <option key={room.id} value={room.id}>{room.project} · {room.name}</option>)}
      </Select>
    </label>
    {loading && <p role="status" className="text-sm">{t('agentMemory.loading')}</p>}
    {error && <p role="alert" className="text-sm text-[var(--color-destructive)]">{error}</p>}
    {notice && <p role="status" className="text-sm text-[var(--color-brand-text)]">{notice}</p>}
    {roomId && snapshot?.room_id === roomId && <>
      {snapshot.ephemeral && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentMemory.ephemeral')}</p>}
      <label className="block space-y-1 text-sm">
        <span>{t('agentMemory.notes', { room: snapshot.room_name })}</span>
        <Textarea value={draft} onChange={event => { setDraft(event.target.value); setNotice(null) }} disabled={busy || loading} rows={7} maxLength={262144} />
      </label>
      {dirty && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentMemory.unsaved')}</p>}
      <div className="flex flex-wrap gap-2">
        <Button className="min-h-11" disabled={busy || loading || !dirty} onClick={() => void saveMemory('save')}>{t('common.save')}</Button>
        <Button variant="outline" className="min-h-11" disabled={busy || loading} onClick={() => void loadMemory()}>{dirty ? t('agentMemory.reload') : t('common.refresh')}</Button>
        <Button variant="ghost" className="min-h-11" disabled={busy || loading || !snapshot.memory_md || dirty} onClick={() => void saveMemory('clear')}>{t('agentMemory.clear')}</Button>
      </div>
    </>}
    {!roomId && !loading && <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentMemory.chooseHint')}</p>}
    {legacy && <details className="rounded-[var(--radius-sm)] border border-[var(--color-border)] p-3 space-y-3">
      <summary className="cursor-pointer text-sm min-h-11 flex items-center">{t('agentMemory.archive')}</summary>
      <p className="text-sm text-[var(--color-foreground-muted)]">{t('agentMemory.archiveHint')}</p>
      {editingLegacy ? <Textarea aria-label={t('agentMemory.archive')} value={legacyDraft} onChange={event => setLegacyDraft(event.target.value)} disabled={busy || loading} rows={7} maxLength={262144} /> : <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-sm">{legacy}</pre>}
      <div className="flex flex-wrap gap-2">
        {editingLegacy ? <>
          <Button className="min-h-11" disabled={busy || loading || legacyDraft === legacy} onClick={() => void saveLegacy()}>{t('common.save')}</Button>
          <Button variant="outline" className="min-h-11" disabled={busy} onClick={() => { setLegacyDraft(legacy); setEditingLegacy(false) }}>{t('common.cancel')}</Button>
        </> : <>
          <Button variant="outline" className="min-h-11" disabled={busy || loading} onClick={() => setEditingLegacy(true)}>{t('agentMemory.editArchive')}</Button>
          <Button variant="outline" className="min-h-11" onClick={exportLegacy}>{t('agentMemory.export')}</Button>
        </>}
        {snapshot?.room_id === roomId && roomId && <Button variant="outline" className="min-h-11" disabled={busy || loading || dirty || editingLegacy} onClick={() => void saveMemory('import-legacy')}>{t('agentMemory.import')}</Button>}
      </div>
    </details>}
    {error && !roomId && <Button variant="outline" className="min-h-11" disabled={busy || loading || editingLegacy} onClick={() => setCatalogReload(value => value + 1)}>{t('common.retry')}</Button>}
  </div>
}
