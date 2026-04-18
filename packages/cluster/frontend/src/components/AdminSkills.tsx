import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Plus, Trash2, BookOpen, RefreshCw, Check, X, Eye, History, Share2, Bot,
  Search as SearchIcon, AlertCircle,
} from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useAgents } from '@/hooks/useAgents'

/**
 * Skill library admin page (#119 / #127 / #125).
 *
 * Phase 2 adds the approve workflow: every registered skill lands in
 * "pending" and must be approved before it can be attached. Rejecting
 * an approved skill clears it from agents on the next reconcile.
 *
 * Tabs group by derived status — the server computes this from
 * ``approved_by`` + the latest audit action, so the client only
 * needs to pass ``?status=`` through.
 */

type SkillStatus = 'pending' | 'approved' | 'rejected'

interface Skill {
  id: string
  source: string
  name: string
  pinned_rev: string
  scripts_detected: string[]
  content_hash: string
  approved_by: string | null
  approved_at: string | null
  // #120 — non-null when an agent authored this skill via MCP. Null
  // for shared library entries (admin-registered or post-Promote).
  created_by_agent_id: string | null
  fetched_at: string
  status: SkillStatus
  attached_agent_ids: string[]
  // #126 — merged from the server's in-memory stale cache. ``true``
  // when upstream HEAD has moved past ``pinned_rev``; the UI flips on
  // an "Update available" badge to prompt admin refresh.
  stale: boolean
}

// #126 — one skills.sh search hit.
interface SearchHit {
  id: string
  skillId: string
  name: string
  installs: number
  source: string
}

interface SkillPreview {
  id: string
  source: string
  name: string
  pinned_rev: string
  skill_md: string
  extra_files: string[]
  content_hash: string
  status: SkillStatus
}

interface AuditEntry {
  id: string
  action: string
  actor_user_id: string | null
  at: string
  detail: Record<string, unknown>
}

const STATUS_LABEL: Record<SkillStatus, string> = {
  pending: '대기',
  approved: '승인',
  rejected: '거부',
}

const STATUS_BADGE_CLASS: Record<SkillStatus, string> = {
  pending:
    'border-[rgba(0,0,0,0.12)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)]',
  approved:
    'border-[rgba(0,0,0,0.12)] bg-[color:color-mix(in_srgb,var(--color-success)_10%,transparent)] text-[var(--color-success)]',
  rejected:
    'border-[rgba(0,0,0,0.12)] bg-[color:color-mix(in_srgb,var(--color-danger)_10%,transparent)] text-[var(--color-danger)]',
}

export default function AdminSkills() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { agents, fetchAgents } = useAgents()

  const [registerOpen, setRegisterOpen] = useState(false)
  const [regSource, setRegSource] = useState('')
  const [regName, setRegName] = useState('')
  const [regRev, setRegRev] = useState('HEAD')
  const [regBusy, setRegBusy] = useState(false)
  const [regError, setRegError] = useState<string | null>(null)

  const [attachTarget, setAttachTarget] = useState<Skill | null>(null)
  const [attachError, setAttachError] = useState<string | null>(null)

  const [previewTarget, setPreviewTarget] = useState<Skill | null>(null)
  const [preview, setPreview] = useState<SkillPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  const [auditTarget, setAuditTarget] = useState<Skill | null>(null)
  const [audits, setAudits] = useState<AuditEntry[]>([])
  const [auditLoading, setAuditLoading] = useState(false)

  const [activeTab, setActiveTab] = useState<SkillStatus>('pending')

  // #126 — search dialog state
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchHit[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  // Track which rows are mid-register so the button can show a
  // per-row spinner (register can take a few seconds — N+1 raw
  // fetches per skill directory).
  const [registeringIds, setRegisteringIds] = useState<Set<string>>(new Set())
  // Card-level refresh spinners, same idea.
  const [refreshingIds, setRefreshingIds] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Fetch all three statuses in parallel so the tab badges update
      // in lockstep (otherwise a click-to-approve briefly desyncs the
      // counts in the opposite tab).
      const resp = await apiFetch('/api/v1/admin/skills')
      if (!resp.ok) throw new Error(`GET /skills → ${resp.status}`)
      setSkills(await resp.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    void fetchAgents()
  }, [load, fetchAgents])

  const byStatus = useMemo(() => {
    const groups: Record<SkillStatus, Skill[]> = {
      pending: [],
      approved: [],
      rejected: [],
    }
    for (const skill of skills) {
      groups[skill.status].push(skill)
    }
    return groups
  }, [skills])

  const handleRegister = useCallback(async () => {
    if (!regSource.trim() || !regName.trim()) return
    setRegBusy(true)
    setRegError(null)
    try {
      const resp = await apiFetch('/api/v1/admin/skills', {
        method: 'POST',
        body: JSON.stringify({
          source: regSource.trim(),
          name: regName.trim(),
          rev: regRev.trim() || 'HEAD',
        }),
      })
      if (!resp.ok) {
        let detail = `Register failed (${resp.status})`
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        throw new Error(detail)
      }
      setRegisterOpen(false)
      setRegSource('')
      setRegName('')
      setRegRev('HEAD')
      // New skill lands in the pending tab by default — switch there
      // so the admin immediately sees what they just created.
      setActiveTab('pending')
      await load()
    } catch (e) {
      setRegError(e instanceof Error ? e.message : String(e))
    } finally {
      setRegBusy(false)
    }
  }, [regSource, regName, regRev, load])

  const handleApprove = useCallback(async (skill: Skill) => {
    const resp = await apiFetch(
      `/api/v1/admin/skills/${skill.id}/approve`,
      { method: 'POST' },
    )
    if (resp.ok) await load()
  }, [load])

  const handleReject = useCallback(async (skill: Skill) => {
    if (
      skill.attached_agent_ids.length > 0 &&
      !window.confirm(
        `"${skill.name}" 스킬이 ${skill.attached_agent_ids.length}개 에이전트에 연결되어 있습니다. 거부하면 다음 spawn 에서 제거됩니다. 계속할까요?`,
      )
    ) {
      return
    }
    const resp = await apiFetch(
      `/api/v1/admin/skills/${skill.id}/reject`,
      { method: 'POST' },
    )
    if (resp.ok) await load()
  }, [load])

  const handleDelete = useCallback(async (skill: Skill) => {
    if (!window.confirm(`"${skill.name}" 스킬을 삭제하시겠습니까? 모든 attachment 도 함께 제거됩니다.`)) {
      return
    }
    const resp = await apiFetch(`/api/v1/admin/skills/${skill.id}`, { method: 'DELETE' })
    if (resp.status === 204) await load()
  }, [load])

  // #126 — run a skills.sh search. Called on open and on every
  // submit; TTL-cached on the server so re-opens are cheap.
  const runSearch = useCallback(async (query: string) => {
    setSearchLoading(true)
    setSearchError(null)
    try {
      const qs = new URLSearchParams({ q: query, limit: '20' })
      const resp = await apiFetch(`/api/v1/admin/skills/search?${qs.toString()}`)
      if (!resp.ok) {
        let detail = `Search failed (${resp.status})`
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        throw new Error(detail)
      }
      setSearchResults(await resp.json())
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e))
      setSearchResults([])
    } finally {
      setSearchLoading(false)
    }
  }, [])

  // Register a skill directly from a search result. Uses the
  // skills.sh ``source`` + ``skillId`` as (source, name) for the
  // server — matches how a manual admin register would work.
  const registerFromSearch = useCallback(async (hit: SearchHit) => {
    setRegisteringIds(prev => new Set(prev).add(hit.id))
    try {
      const resp = await apiFetch('/api/v1/admin/skills', {
        method: 'POST',
        body: JSON.stringify({
          source: hit.source,
          name: hit.skillId,
          rev: 'HEAD',
        }),
      })
      if (resp.ok) {
        setActiveTab('pending')
        await load()
      } else {
        let detail = `Register failed (${resp.status})`
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        setSearchError(detail)
      }
    } finally {
      setRegisteringIds(prev => {
        const next = new Set(prev)
        next.delete(hit.id)
        return next
      })
    }
  }, [load])

  // #126 — refresh a single skill against upstream HEAD. Phase 2 gate
  // means a SHA change mints a new pending row — the list reloads to
  // surface it in the pending tab.
  const handleRefresh = useCallback(async (skill: Skill) => {
    setRefreshingIds(prev => new Set(prev).add(skill.id))
    try {
      const resp = await apiFetch(
        `/api/v1/admin/skills/${skill.id}/refresh`,
        { method: 'POST' },
      )
      if (resp.ok) {
        await load()
      } else {
        let detail = `Refresh failed (${resp.status})`
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        setError(detail)
      }
    } finally {
      setRefreshingIds(prev => {
        const next = new Set(prev)
        next.delete(skill.id)
        return next
      })
    }
  }, [load])

  // #120 — move an agent-authored skill into the shared library so any
  // agent can attach to it afterwards.
  const handlePromote = useCallback(async (skill: Skill) => {
    if (!window.confirm(
      `"${skill.name}" 을 공유 라이브러리로 승격하시겠습니까? ` +
      `다른 에이전트도 이 스킬에 attach 할 수 있게 됩니다.`
    )) return
    const resp = await apiFetch(
      `/api/v1/admin/skills/${skill.id}/promote`,
      { method: 'POST' },
    )
    if (resp.ok) await load()
  }, [load])

  const openPreview = useCallback(async (skill: Skill) => {
    setPreviewTarget(skill)
    setPreview(null)
    setPreviewLoading(true)
    try {
      const resp = await apiFetch(`/api/v1/admin/skills/${skill.id}/preview`)
      if (resp.ok) setPreview(await resp.json())
    } finally {
      setPreviewLoading(false)
    }
  }, [])

  const openAudits = useCallback(async (skill: Skill) => {
    setAuditTarget(skill)
    setAudits([])
    setAuditLoading(true)
    try {
      const resp = await apiFetch(`/api/v1/admin/skills/${skill.id}/audits`)
      if (resp.ok) setAudits(await resp.json())
    } finally {
      setAuditLoading(false)
    }
  }, [])

  const toggleAttach = useCallback(async (skill: Skill, agentId: string) => {
    setAttachError(null)
    const isAttached = skill.attached_agent_ids.includes(agentId)
    if (isAttached) {
      const resp = await apiFetch(
        `/api/v1/admin/skills/${skill.id}/attach/${agentId}`,
        { method: 'DELETE' },
      )
      if (resp.status === 204) {
        await load()
        // refresh the open dialog's skill snapshot so checkboxes flip.
        setAttachTarget(prev => prev && { ...prev, attached_agent_ids: prev.attached_agent_ids.filter(id => id !== agentId) })
      }
    } else {
      const resp = await apiFetch(
        `/api/v1/admin/skills/${skill.id}/attach`,
        { method: 'POST', body: JSON.stringify({ agent_id: agentId }) },
      )
      if (resp.status === 204) {
        await load()
        setAttachTarget(prev => prev && { ...prev, attached_agent_ids: [...prev.attached_agent_ids, agentId] })
      } else if (resp.status === 409) {
        // Most common reason — skill isn't approved. The server's
        // detail message is the right thing to surface verbatim.
        let detail = '미승인 스킬은 attach 할 수 없습니다.'
        try {
          const body = await resp.json()
          if (body?.detail) detail = body.detail
        } catch { /* ignore */ }
        setAttachError(detail)
      }
    }
  }, [load])

  return (
    <div className="max-w-4xl p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--color-foreground)]">Skills</h1>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            Register shared skills from GitHub, approve them, and attach to agents.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSearchOpen(true)
              if (searchResults.length === 0) void runSearch('')
            }}
            data-testid="admin-skill-search-open"
          >
            <SearchIcon className="mr-1 h-3.5 w-3.5" /> Search skills.sh
          </Button>
          <Button size="sm" onClick={() => setRegisterOpen(true)}>
            <Plus className="mr-1 h-3.5 w-3.5" /> Register skill
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[color:color-mix(in_srgb,var(--color-danger)_8%,transparent)] p-3 text-sm text-[var(--color-danger)]">
          {error}
        </div>
      )}

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as SkillStatus)}>
        <TabsList>
          <TabsTrigger value="pending">
            대기 {byStatus.pending.length > 0 && (
              <span className="ml-1 text-xs text-[var(--color-foreground-muted)]">
                {byStatus.pending.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="approved">
            승인 {byStatus.approved.length > 0 && (
              <span className="ml-1 text-xs text-[var(--color-foreground-muted)]">
                {byStatus.approved.length}
              </span>
            )}
          </TabsTrigger>
          <TabsTrigger value="rejected">
            거부 {byStatus.rejected.length > 0 && (
              <span className="ml-1 text-xs text-[var(--color-foreground-muted)]">
                {byStatus.rejected.length}
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        {(['pending', 'approved', 'rejected'] as SkillStatus[]).map(status => (
          <TabsContent key={status} value={status} className="space-y-2">
            {byStatus[status].length === 0 && !loading ? (
              <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-6 py-10 text-center shadow-[var(--shadow-card)]">
                <BookOpen
                  className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]"
                  strokeWidth={1.5}
                />
                <p className="text-sm text-[var(--color-foreground-muted)]">
                  {status === 'pending' && '대기 중인 스킬이 없습니다. 새 스킬을 등록하면 이 탭에 표시됩니다.'}
                  {status === 'approved' && '승인된 스킬이 없습니다.'}
                  {status === 'rejected' && '거부된 스킬이 없습니다.'}
                </p>
              </div>
            ) : (
              byStatus[status].map(skill => (
                <div
                  key={skill.id}
                  className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-4 py-3 shadow-[var(--shadow-card)]"
                  data-testid={`admin-skill-row-${skill.id}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
                          {skill.name}
                        </h3>
                        <Badge
                          variant="outline"
                          className={STATUS_BADGE_CLASS[skill.status]}
                          data-testid={`admin-skill-status-${skill.id}`}
                        >
                          {STATUS_LABEL[skill.status]}
                        </Badge>
                        <Badge variant="outline">
                          <code className="text-[11px]">{skill.pinned_rev.slice(0, 8)}</code>
                        </Badge>
                        {skill.scripts_detected.length > 0 && (
                          <Badge
                            variant="outline"
                            className="text-[var(--color-foreground-muted)]"
                            title={skill.scripts_detected.join('\n')}
                          >
                            +{skill.scripts_detected.length} files
                          </Badge>
                        )}
                        {skill.created_by_agent_id !== null && (
                          <Badge
                            variant="outline"
                            className="text-[var(--color-foreground-muted)]"
                            title={`Authored by agent ${skill.created_by_agent_id}`}
                            data-testid={`admin-skill-agent-authored-${skill.id}`}
                          >
                            <Bot className="mr-1 h-3 w-3" /> agent
                          </Badge>
                        )}
                        {skill.stale && (
                          <Badge
                            variant="outline"
                            className="border-[rgba(0,0,0,0.12)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] text-[var(--color-warning)]"
                            title="Upstream HEAD has moved — click Refresh to re-fetch"
                            data-testid={`admin-skill-stale-${skill.id}`}
                          >
                            <AlertCircle className="mr-1 h-3 w-3" />
                            Update available
                          </Badge>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-[var(--color-foreground-muted)]">
                        <code>{skill.source}</code>
                      </p>
                      <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                        {skill.attached_agent_ids.length} agent{skill.attached_agent_ids.length === 1 ? '' : 's'} attached
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void openPreview(skill)}
                        data-testid={`admin-skill-preview-${skill.id}`}
                      >
                        <Eye className="mr-1 h-3.5 w-3.5" /> Preview
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void openAudits(skill)}
                        data-testid={`admin-skill-audits-${skill.id}`}
                      >
                        <History className="mr-1 h-3.5 w-3.5" /> History
                      </Button>
                      {skill.status !== 'approved' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleApprove(skill)}
                          data-testid={`admin-skill-approve-${skill.id}`}
                        >
                          <Check className="mr-1 h-3.5 w-3.5 text-[var(--color-success)]" />
                          Approve
                        </Button>
                      )}
                      {skill.status !== 'rejected' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleReject(skill)}
                          data-testid={`admin-skill-reject-${skill.id}`}
                        >
                          <X className="mr-1 h-3.5 w-3.5 text-[var(--color-danger)]" />
                          Reject
                        </Button>
                      )}
                      {skill.status === 'approved' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setAttachTarget(skill)}
                          data-testid={`admin-skill-attach-${skill.id}`}
                        >
                          Attach
                        </Button>
                      )}
                      {skill.created_by_agent_id !== null && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handlePromote(skill)}
                          data-testid={`admin-skill-promote-${skill.id}`}
                          title="Promote to shared library"
                        >
                          <Share2 className="mr-1 h-3.5 w-3.5" /> Promote
                        </Button>
                      )}
                      {/* #126 — refresh against upstream HEAD. Hidden for
                          agent-authored rows since their ``source`` has
                          no upstream to poll. */}
                      {skill.created_by_agent_id === null && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={refreshingIds.has(skill.id)}
                          onClick={() => void handleRefresh(skill)}
                          data-testid={`admin-skill-refresh-${skill.id}`}
                          title="Re-fetch from upstream HEAD"
                        >
                          <RefreshCw className={`mr-1 h-3.5 w-3.5 ${refreshingIds.has(skill.id) ? 'animate-spin' : ''}`} />
                          Refresh
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleDelete(skill)}
                        aria-label={`Delete ${skill.name}`}
                        data-testid={`admin-skill-delete-${skill.id}`}
                      >
                        <Trash2 className="h-4 w-4 text-[var(--color-danger)]" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </TabsContent>
        ))}
      </Tabs>

      {/* Register dialog */}
      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Register skill from GitHub</DialogTitle>
            <DialogDescription>
              The repo must follow the <code>skills/&lt;name&gt;/SKILL.md</code> layout.
              We pin the commit SHA at registration so spawns stay reproducible.
              New skills start in <strong>pending</strong> and need approval before
              they can be attached.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div>
              <Label htmlFor="skill-source">Source (owner/repo)</Label>
              <Input
                id="skill-source"
                placeholder="vercel-labs/agent-skills"
                value={regSource}
                onChange={e => setRegSource(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="skill-name">Skill name</Label>
              <Input
                id="skill-name"
                placeholder="web-design-guidelines"
                value={regName}
                onChange={e => setRegName(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="skill-rev">Revision (optional)</Label>
              <Input
                id="skill-rev"
                placeholder="HEAD / branch / tag / SHA"
                value={regRev}
                onChange={e => setRegRev(e.target.value)}
              />
            </div>
            {regError && (
              <p className="text-xs text-[var(--color-danger)]">{regError}</p>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setRegisterOpen(false)}
              disabled={regBusy}
            >
              Cancel
            </Button>
            <Button
              onClick={() => void handleRegister()}
              disabled={regBusy || !regSource.trim() || !regName.trim()}
            >
              {regBusy ? 'Registering…' : 'Register'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Preview dialog */}
      <Dialog open={previewTarget !== null} onOpenChange={(o) => { if (!o) { setPreviewTarget(null); setPreview(null) } }}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              Preview <code>{previewTarget?.name}</code>
            </DialogTitle>
            <DialogDescription>
              SKILL.md body + 보조 파일 목록. 승인 결정 전에 내용을 확인하세요.
            </DialogDescription>
          </DialogHeader>
          {previewLoading && (
            <p className="text-sm text-[var(--color-foreground-muted)]">Loading…</p>
          )}
          {preview && (
            <div className="space-y-3">
              <div>
                <Label className="text-xs">SKILL.md</Label>
                <pre className="mt-1 max-h-72 overflow-auto rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[var(--color-surface-alt)] p-3 text-xs">
                  {preview.skill_md}
                </pre>
              </div>
              {preview.extra_files.length > 0 && (
                <div>
                  <Label className="text-xs">
                    보조 파일 ({preview.extra_files.length})
                  </Label>
                  <ul className="mt-1 max-h-40 overflow-auto rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[var(--color-surface-alt)] p-2 text-xs">
                    {preview.extra_files.map(path => (
                      <li key={path} className="font-mono">
                        {path}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button onClick={() => { setPreviewTarget(null); setPreview(null) }}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Audit drawer (rendered as a side dialog for simplicity) */}
      <Dialog open={auditTarget !== null} onOpenChange={(o) => { if (!o) { setAuditTarget(null); setAudits([]) } }}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>
              History <code>{auditTarget?.name}</code>
            </DialogTitle>
            <DialogDescription>
              이 스킬의 변경 이력 (가장 최근부터).
            </DialogDescription>
          </DialogHeader>
          {auditLoading && (
            <p className="text-sm text-[var(--color-foreground-muted)]">Loading…</p>
          )}
          {!auditLoading && audits.length === 0 && (
            <p className="text-sm text-[var(--color-foreground-muted)]">
              이력이 없습니다.
            </p>
          )}
          {!auditLoading && audits.length > 0 && (
            <ul className="max-h-80 space-y-2 overflow-auto">
              {audits.map(a => (
                <li
                  key={a.id}
                  className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] p-2 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant="outline">{a.action}</Badge>
                    <span className="text-[var(--color-foreground-muted)]">
                      {new Date(a.at).toLocaleString()}
                    </span>
                  </div>
                  {Object.keys(a.detail).length > 0 && (
                    <pre className="mt-1 overflow-x-auto text-[11px] text-[var(--color-foreground-muted)]">
                      {JSON.stringify(a.detail, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          )}
          <DialogFooter>
            <Button onClick={() => { setAuditTarget(null); setAudits([]) }}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Attach dialog */}
      <Dialog
        open={attachTarget !== null}
        onOpenChange={(o) => { if (!o) { setAttachTarget(null); setAttachError(null) } }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Attach <code>{attachTarget?.name}</code>
            </DialogTitle>
            <DialogDescription>
              Toggle the agents that should receive this skill's directory on their next spawn.
            </DialogDescription>
          </DialogHeader>
          {attachError && (
            <div className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[color:color-mix(in_srgb,var(--color-danger)_8%,transparent)] p-2 text-xs text-[var(--color-danger)]">
              {attachError}
            </div>
          )}
          <div className="max-h-80 space-y-1 overflow-auto py-2">
            {agents.length === 0 ? (
              <p className="text-sm text-[var(--color-foreground-muted)]">
                No agents yet — create an agent first under Machines.
              </p>
            ) : (
              agents.map(agent => {
                const attached = attachTarget?.attached_agent_ids.includes(agent.id) ?? false
                return (
                  <label
                    key={agent.id}
                    className="flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-1.5 hover:bg-black/5 cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={attached}
                      onChange={() => attachTarget && void toggleAttach(attachTarget, agent.id)}
                      data-testid={`admin-skill-agent-${agent.id}`}
                    />
                    <span className="text-sm text-[var(--color-foreground)]">{agent.name}</span>
                    <span className="text-xs text-[var(--color-foreground-muted)]">
                      {agent.engine}
                    </span>
                  </label>
                )
              })
            )}
          </div>
          <DialogFooter>
            <Button onClick={() => { setAttachTarget(null); setAttachError(null) }}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* #126 — skills.sh search dialog */}
      <Dialog
        open={searchOpen}
        onOpenChange={(o) => {
          if (!o) { setSearchOpen(false); setSearchError(null) }
        }}
      >
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Search skills.sh</DialogTitle>
            <DialogDescription>
              검색어를 입력해 skills.sh 에서 스킬을 찾고 한 번 클릭으로 등록하세요.
              등록된 스킬은 <strong>대기</strong> 탭에 들어가 승인 후 attach 가능합니다.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 py-2">
            <Input
              placeholder="design / slack / python …"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') void runSearch(searchQuery)
              }}
              data-testid="admin-skill-search-input"
            />
            <Button
              onClick={() => void runSearch(searchQuery)}
              disabled={searchLoading}
              data-testid="admin-skill-search-submit"
            >
              {searchLoading ? '검색 중…' : 'Search'}
            </Button>
          </div>
          {searchError && (
            <div className="rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] bg-[color:color-mix(in_srgb,var(--color-danger)_8%,transparent)] p-2 text-xs text-[var(--color-danger)]">
              {searchError}
            </div>
          )}
          <div className="max-h-[26rem] overflow-auto">
            {searchResults.length === 0 && !searchLoading ? (
              <p className="py-6 text-center text-sm text-[var(--color-foreground-muted)]">
                검색 결과가 없습니다.
              </p>
            ) : (
              <ul className="space-y-1">
                {searchResults.map(hit => (
                  <li
                    key={hit.id}
                    className="flex items-center justify-between gap-3 rounded-[var(--radius-sm)] border border-[rgba(0,0,0,0.1)] px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <span className="truncate text-sm font-medium text-[var(--color-foreground)]">
                          {hit.name}
                        </span>
                        <span className="text-xs text-[var(--color-foreground-muted)]">
                          {hit.installs} installs
                        </span>
                      </div>
                      <p className="truncate text-xs text-[var(--color-foreground-muted)]">
                        <code>{hit.source}</code> · <code>{hit.skillId}</code>
                      </p>
                    </div>
                    <Button
                      size="sm"
                      disabled={registeringIds.has(hit.id)}
                      onClick={() => void registerFromSearch(hit)}
                      data-testid={`admin-skill-register-from-search-${hit.id}`}
                    >
                      {registeringIds.has(hit.id) ? 'Registering…' : 'Register'}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setSearchOpen(false)}
            >
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
