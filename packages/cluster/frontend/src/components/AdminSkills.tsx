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
  Plus, Trash2, BookOpen, RefreshCw, Check, X, Eye, History,
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
  fetched_at: string
  status: SkillStatus
  attached_agent_ids: string[]
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
    </div>
  )
}
