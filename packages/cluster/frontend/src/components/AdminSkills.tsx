import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription,
} from '@/components/ui/dialog'
import { Plus, Trash2, BookOpen, RefreshCw } from 'lucide-react'
import { apiFetch } from '@/lib/api'
import { useAgents } from '@/hooks/useAgents'

/**
 * Skill library admin page (#119 Phase 1).
 *
 * Lets admins register a skill from a public GitHub repo, inspect the
 * pinned commit, and attach the skill to one or more agents. Phase 1
 * only materializes SKILL.md — the ``scripts_detected`` column is
 * shown as a count so admins know "this skill ships N extra files
 * we didn't install yet".
 */

interface Skill {
  id: string
  source: string
  name: string
  pinned_rev: string
  scripts_detected: string[]
  content_hash: string
  approved_by: string | null
  fetched_at: string
  attached_agent_ids: string[]
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

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
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
        // Surface server-side detail when available — admins debugging
        // a wrong repo layout care about "SKILL.md not found" more
        // than a bare 400.
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
      await load()
    } catch (e) {
      setRegError(e instanceof Error ? e.message : String(e))
    } finally {
      setRegBusy(false)
    }
  }, [regSource, regName, regRev, load])

  const handleDelete = useCallback(async (skillId: string, label: string) => {
    if (!window.confirm(`"${label}" 스킬을 삭제하시겠습니까? 모든 attachment 도 함께 제거됩니다.`)) {
      return
    }
    const resp = await apiFetch(`/api/v1/admin/skills/${skillId}`, { method: 'DELETE' })
    if (resp.status === 204) await load()
  }, [load])

  const toggleAttach = useCallback(async (skill: Skill, agentId: string) => {
    const isAttached = skill.attached_agent_ids.includes(agentId)
    if (isAttached) {
      const resp = await apiFetch(
        `/api/v1/admin/skills/${skill.id}/attach/${agentId}`,
        { method: 'DELETE' },
      )
      if (resp.status === 204) await load()
    } else {
      const resp = await apiFetch(
        `/api/v1/admin/skills/${skill.id}/attach`,
        { method: 'POST', body: JSON.stringify({ agent_id: agentId }) },
      )
      if (resp.status === 204) await load()
    }
  }, [load])

  return (
    <div className="max-w-4xl p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--color-foreground)]">Skills</h1>
          <p className="text-sm text-[var(--color-foreground-muted)]">
            Register shared skills from GitHub and attach them to agents.
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

      {skills.length === 0 && !loading ? (
        <div className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-6 py-10 text-center shadow-[var(--shadow-card)]">
          <BookOpen
            className="mx-auto mb-3 h-8 w-8 text-[var(--color-foreground-subtle)]"
            strokeWidth={1.5}
          />
          <p className="text-sm text-[var(--color-foreground-muted)]">
            아직 등록된 스킬이 없습니다. 상단의 "Register skill" 버튼으로 GitHub
            레포를 등록하세요.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map(skill => (
            <div
              key={skill.id}
              className="rounded-[var(--radius-lg)] border border-[rgba(0,0,0,0.1)] bg-white px-4 py-3 shadow-[var(--shadow-card)]"
              data-testid={`admin-skill-row-${skill.id}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-[var(--color-foreground)]">
                      {skill.name}
                    </h3>
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
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setAttachTarget(skill)}
                    data-testid={`admin-skill-attach-${skill.id}`}
                  >
                    Attach
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void handleDelete(skill.id, skill.name)}
                    aria-label={`Delete ${skill.name}`}
                    data-testid={`admin-skill-delete-${skill.id}`}
                  >
                    <Trash2 className="h-4 w-4 text-[var(--color-danger)]" />
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Register dialog */}
      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Register skill from GitHub</DialogTitle>
            <DialogDescription>
              The repo must follow the <code>skills/&lt;name&gt;/SKILL.md</code> layout.
              We pin the commit SHA at registration so spawns stay reproducible.
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

      {/* Attach dialog */}
      <Dialog open={attachTarget !== null} onOpenChange={(o) => { if (!o) setAttachTarget(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Attach <code>{attachTarget?.name}</code>
            </DialogTitle>
            <DialogDescription>
              Toggle the agents that should receive this skill's <code>SKILL.md</code>
              on their next spawn.
            </DialogDescription>
          </DialogHeader>
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
            <Button onClick={() => setAttachTarget(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
