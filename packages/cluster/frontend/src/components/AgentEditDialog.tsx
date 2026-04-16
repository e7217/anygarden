/**
 * AgentEditDialog — admin UI for the per-agent file manifest.
 *
 * Two panels:
 *
 * 1. AGENTS.md — the system-prompt/role/rules body the materializer
 *    writes to ``agent_root/AGENTS.md``. Nullable so the admin can
 *    clear it.
 *
 * 2. Files tree — every ``agent_files`` row, grouped by top-level
 *    prefix (``skills/``, ``.codex/``, ``.claude/``, ``.gemini/``,
 *    ``.openhands/``) and editable as plain text. The backend
 *    whitelist in ``doorae/agent_files.py`` rejects anything else,
 *    so the UI mirrors those prefixes in its "new file" picker.
 *
 * Save semantics:
 *
 * - Saves happen in bulk on the Save button so the admin can edit
 *   several files without network churn.
 * - Changes take effect on the NEXT spawn, not immediately — the
 *   running subprocess is not hot-reloaded (each engine re-reads
 *   its manifest at a different moment, so making "update =
 *   restart" implicit would be surprising). A hint line at the
 *   bottom of the dialog reminds the admin.
 *
 * Style: follows DESIGN.md (warm neutral palette, whisper borders,
 * near-black text, single-accent brand color).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Plus, Trash2, FileText } from 'lucide-react'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile } from '@/hooks/useAgents'

// Allowed top-level prefixes from the server-side whitelist.
// Must stay in sync with ``doorae-server/doorae/agent_files.py``.
const ALLOWED_PREFIXES: readonly string[] = [
  'skills/',
  '.codex/',
  '.claude/',
  '.gemini/',
  '.openhands/',
]

// Friendly label for each prefix grouping in the file list.
const PREFIX_LABELS: Record<string, string> = {
  'skills/': 'Skills',
  '.codex/': 'Codex config',
  '.claude/': 'Claude Code config',
  '.gemini/': 'Gemini CLI config',
  '.openhands/': 'OpenHands config',
}

type WorkingFile = AgentFile & {
  // Tracks per-file edit state so we only PUT what actually changed
  // and only DELETE what the admin actively removed. ``originalContent``
  // is null for files the admin created in this session.
  originalContent: string | null
  dirty: boolean
  // Marked for deletion in the working copy; removed from the UI
  // list but still present in state so the Save pass can issue
  // DELETE requests for them.
  deleted: boolean
}

interface Props {
  agent: Agent | null
  open: boolean
  onOpenChange: (open: boolean) => void
  fetchAgentFiles: (id: string) => Promise<AgentFile[]>
  updateAgent: (
    id: string,
    patch: { name?: string; agents_md?: string | null; agents_md_set?: boolean },
  ) => Promise<Agent>
  upsertAgentFile: (id: string, path: string, content: string) => Promise<AgentFile>
  deleteAgentFile: (id: string, path: string) => Promise<void>
}

export default function AgentEditDialog({
  agent,
  open,
  onOpenChange,
  fetchAgentFiles,
  updateAgent,
  upsertAgentFile,
  deleteAgentFile,
}: Props) {
  const [agentsMd, setAgentsMd] = useState<string>('')
  const [agentsMdDirty, setAgentsMdDirty] = useState(false)
  const [files, setFiles] = useState<WorkingFile[]>([])
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showNewFileForm, setShowNewFileForm] = useState(false)
  const [newFilePath, setNewFilePath] = useState('skills/')

  // Pull the canonical file list from the server into the working
  // copy. Used in two places with slightly different behavior:
  //
  // - ``loadInitial`` (on open): also reads ``agentsMd`` from the
  //   parent-supplied ``agent`` prop, because that's the freshest
  //   the dialog has access to at open time.
  //
  // - ``resyncAfterSave`` (after Save succeeds): leaves the local
  //   ``agentsMd`` state as-is — it's already been flushed to the
  //   server and the ``agent`` prop is a STALE snapshot from when
  //   the dialog was opened, so re-reading it would clobber the
  //   edit we just saved. Same for the file contents: the textarea
  //   already holds the bytes we just saved, so we just clear the
  //   dirty flags and drop rows that were marked deleted.
  const fetchFilesIntoWorking = useCallback(async (agentId: string) => {
    const rows = await fetchAgentFiles(agentId)
    return rows.map<WorkingFile>(r => ({
      path: r.path,
      content: r.content,
      updated_at: r.updated_at,
      originalContent: r.content,
      dirty: false,
      deleted: false,
    }))
  }, [fetchAgentFiles])

  const loadInitial = useCallback(async () => {
    if (!agent) return
    setLoading(true)
    setError(null)
    try {
      const working = await fetchFilesIntoWorking(agent.id)
      setFiles(working)
      setAgentsMd(agent.agents_md ?? '')
      setAgentsMdDirty(false)
      setSelectedPath(prev => {
        // Keep the previously-selected path if it still exists,
        // otherwise fall back to the first file.
        if (prev && working.some(f => f.path === prev)) return prev
        return working[0]?.path ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [agent, fetchFilesIntoWorking])

  const resyncAfterSave = useCallback(async () => {
    if (!agent) return
    try {
      const working = await fetchFilesIntoWorking(agent.id)
      setFiles(working)
      setAgentsMdDirty(false)
      setSelectedPath(prev => {
        if (prev && working.some(f => f.path === prev)) return prev
        return working[0]?.path ?? null
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [agent, fetchFilesIntoWorking])

  useEffect(() => {
    if (open && agent) {
      void loadInitial()
    } else if (!open) {
      // Reset transient state on close so the next open starts clean.
      setShowNewFileForm(false)
      setNewFilePath('skills/')
      setError(null)
    }
  }, [open, agent, loadInitial])

  const visibleFiles = useMemo(
    () => files.filter(f => !f.deleted).sort((a, b) => a.path.localeCompare(b.path)),
    [files],
  )

  // Group visible files by top-level prefix so the admin can see at
  // a glance what kind of file each row is ("Skills" vs
  // "Codex config").
  const groupedFiles = useMemo(() => {
    const groups: Array<{ prefix: string; label: string; files: WorkingFile[] }> = []
    for (const prefix of ALLOWED_PREFIXES) {
      const matching = visibleFiles.filter(f => f.path.startsWith(prefix))
      if (matching.length > 0) {
        groups.push({
          prefix,
          label: PREFIX_LABELS[prefix] ?? prefix,
          files: matching,
        })
      }
    }
    return groups
  }, [visibleFiles])

  const selectedFile = useMemo(
    () => (selectedPath ? files.find(f => f.path === selectedPath) ?? null : null),
    [files, selectedPath],
  )

  // Track "anything changed" to enable/disable the Save button and
  // warn on close. agentsMdDirty covers the top textarea; the files
  // array is scanned for ``dirty`` or ``deleted`` markers.
  const hasChanges = useMemo(
    () => agentsMdDirty || files.some(f => f.dirty || f.deleted),
    [agentsMdDirty, files],
  )

  const handleAgentsMdChange = (value: string) => {
    setAgentsMd(value)
    setAgentsMdDirty(true)
  }

  const handleFileContentChange = (value: string) => {
    if (!selectedPath) return
    setFiles(prev =>
      prev.map(f =>
        f.path === selectedPath
          ? { ...f, content: value, dirty: f.originalContent !== value }
          : f,
      ),
    )
  }

  const handleAddFile = () => {
    const path = newFilePath.trim()
    if (!path) return
    // Client-side validation mirrors a slice of the server-side
    // whitelist so the admin gets immediate feedback. The server
    // has the authoritative check on save.
    if (!ALLOWED_PREFIXES.some(p => path.startsWith(p))) {
      setError(`path must start with one of: ${ALLOWED_PREFIXES.join(', ')}`)
      return
    }
    if (files.some(f => f.path === path && !f.deleted)) {
      setError(`file ${path} already exists`)
      return
    }
    // Un-delete if the admin is re-adding a file they just removed.
    const restored = files.find(f => f.path === path && f.deleted)
    if (restored) {
      setFiles(prev =>
        prev.map(f =>
          f.path === path ? { ...f, deleted: false, dirty: f.content !== f.originalContent } : f,
        ),
      )
    } else {
      setFiles(prev => [
        ...prev,
        {
          path,
          content: '',
          updated_at: new Date().toISOString(),
          originalContent: null,
          dirty: true,
          deleted: false,
        },
      ])
    }
    setSelectedPath(path)
    setShowNewFileForm(false)
    setNewFilePath('skills/')
    setError(null)
  }

  const handleRemoveFile = (path: string) => {
    setFiles(prev => prev.map(f => (f.path === path ? { ...f, deleted: true } : f)))
    if (selectedPath === path) {
      const nextVisible = files.find(f => f.path !== path && !f.deleted)
      setSelectedPath(nextVisible ? nextVisible.path : null)
    }
  }

  const handleSave = async () => {
    if (!agent) return
    setSaving(true)
    setError(null)
    try {
      // 1. agents_md patch — only when actually dirty. Empty string
      //    is a valid value (admin cleared all rules) so we key on
      //    the dirty flag rather than value emptiness.
      if (agentsMdDirty) {
        await updateAgent(agent.id, {
          agents_md: agentsMd === '' ? null : agentsMd,
          agents_md_set: true,
        })
      }

      // 2. New and updated files — PUT upserts them one at a time.
      //    A single bad path 400s that one call; we surface the
      //    error and stop so the admin can fix it without losing
      //    state on the other files.
      for (const f of files) {
        if (f.deleted) continue
        if (f.dirty) {
          await upsertAgentFile(agent.id, f.path, f.content)
        }
      }

      // 3. Deletions — only for files that existed on the server
      //    (have an ``originalContent``). Admin-created files that
      //    were then marked deleted never hit the server in the
      //    first place, so skipping them is correct.
      for (const f of files) {
        if (f.deleted && f.originalContent !== null) {
          await deleteAgentFile(agent.id, f.path)
        }
      }

      // Soft resync after save: pull the fresh ``agent_files``
      // rows so ``updated_at`` values reflect the write, drop
      // rows that were marked ``deleted`` in the working copy,
      // and clear dirty flags. Deliberately does NOT re-read
      // ``agents_md`` — the local state is already authoritative
      // (we just pushed it), and the ``agent`` prop the dialog
      // was opened with is a stale snapshot that would revert
      // the edit we just saved.
      await resyncAfterSave()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>
            Edit manifest
            {agent ? (
              <span className="ml-2 inline-flex items-center gap-1.5 text-sm font-normal text-[var(--color-foreground-muted)]">
                <PresenceDot
                  variant="agent"
                  online={deriveAgentOnline(agent.actual_state)}
                  agentState={agent.actual_state}
                />
                {agent.name} ({agent.engine})
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            Update the agent's system prompt and on-disk files. Changes
            take effect on the next spawn — restart the agent to apply.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="py-8 text-center text-caption text-[var(--color-foreground-muted)]">
            Loading…
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto py-2 space-y-5">
            {/* AGENTS.md ----------------------------------------------- */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="agents-md" className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 text-[var(--color-foreground-muted)]" />
                  AGENTS.md
                </Label>
                {agentsMdDirty ? (
                  <Badge
                    variant="outline"
                    className="bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] border-[color:color-mix(in_srgb,var(--color-brand)_20%,transparent)]"
                  >
                    Unsaved
                  </Badge>
                ) : null}
              </div>
              <textarea
                id="agents-md"
                className="font-mono text-sm flex w-full min-h-[160px] rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-2 text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                placeholder="# Agent role and rules&#10;&#10;Define the agent's role, instructions, and any skill usage conventions here."
                value={agentsMd}
                onChange={e => handleAgentsMdChange(e.target.value)}
                spellCheck={false}
                data-testid="agent-edit-agents-md"
              />
            </section>

            {/* Files tree + editor ----------------------------------- */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Files</Label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowNewFileForm(s => !s)}
                  data-testid="agent-edit-toggle-new-file"
                >
                  <Plus className="mr-1 h-4 w-4" />
                  New file
                </Button>
              </div>

              {showNewFileForm ? (
                <div className="flex gap-2 items-center bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
                  <Input
                    value={newFilePath}
                    onChange={e => setNewFilePath(e.target.value)}
                    placeholder="skills/greeting/SKILL.md"
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddFile()
                      }
                    }}
                    data-testid="agent-edit-new-file-path"
                  />
                  <Button size="sm" onClick={handleAddFile}>Add</Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setShowNewFileForm(false)
                      setNewFilePath('skills/')
                    }}
                  >
                    Cancel
                  </Button>
                </div>
              ) : null}

              <div className="grid grid-cols-[240px_1fr] gap-3 min-h-[280px]">
                {/* Left: file tree */}
                <div className="overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-background)]">
                  {groupedFiles.length === 0 ? (
                    <div className="p-4 text-caption text-[var(--color-foreground-subtle)]">
                      No files yet. Click "New file" to add one.
                    </div>
                  ) : (
                    groupedFiles.map(group => (
                      <div key={group.prefix} className="py-1">
                        <div className="px-3 py-1 text-badge uppercase tracking-wider text-[var(--color-foreground-muted)]">
                          {group.label}
                        </div>
                        {group.files.map(f => {
                          const isSelected = f.path === selectedPath
                          return (
                            <div
                              key={f.path}
                              className={`group flex items-center justify-between px-3 py-1.5 text-sm cursor-pointer transition-colors ${
                                isSelected
                                  ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]'
                                  : 'hover:bg-[var(--color-surface-alt)] text-[var(--color-foreground)]'
                              }`}
                              onClick={() => setSelectedPath(f.path)}
                              data-testid={`agent-edit-file-${f.path}`}
                            >
                              <span className="truncate font-mono text-xs">
                                {f.path.slice(group.prefix.length)}
                                {f.dirty ? <span className="ml-1 opacity-70">•</span> : null}
                              </span>
                              <button
                                type="button"
                                onClick={e => {
                                  e.stopPropagation()
                                  handleRemoveFile(f.path)
                                }}
                                className="opacity-0 group-hover:opacity-100 transition-opacity"
                                title={`Remove ${f.path}`}
                              >
                                <Trash2 className="h-3.5 w-3.5 text-[var(--color-warning)]" />
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    ))
                  )}
                </div>

                {/* Right: file content editor */}
                <div className="flex flex-col">
                  {selectedFile ? (
                    <>
                      <div className="mb-1 font-mono text-xs text-[var(--color-foreground-muted)]">
                        {selectedFile.path}
                      </div>
                      <textarea
                        className="font-mono text-sm flex-1 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-2 text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                        value={selectedFile.content}
                        onChange={e => handleFileContentChange(e.target.value)}
                        spellCheck={false}
                        data-testid="agent-edit-file-content"
                      />
                    </>
                  ) : (
                    <div className="flex-1 flex items-center justify-center text-caption text-[var(--color-foreground-subtle)] border border-[var(--color-border)] rounded-[var(--radius-md)]">
                      Select a file on the left, or click "New file" to add one.
                    </div>
                  )}
                </div>
              </div>
            </section>
          </div>
        )}

        {error ? (
          <div className="mt-2 rounded-[var(--radius-md)] border border-[color:color-mix(in_srgb,var(--color-warning)_30%,transparent)] bg-[color:color-mix(in_srgb,var(--color-warning)_10%,transparent)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {error}
          </div>
        ) : null}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Close
          </Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || saving || loading}
            data-testid="agent-edit-save"
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
