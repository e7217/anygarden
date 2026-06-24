/**
 * ManifestPanel — per-agent file manifest editor (#158).
 *
 * Extracted from AgentEditDialog: same file-tree + editor UI, now
 * rendered as a naked panel inside AgentSettingsDialog. The outer
 * Dialog shell (header, close button, Escape) is owned by
 * AgentSettingsDialog; this panel keeps only its own Save button
 * because the edit flow is modal within the section.
 *
 * Backend-side ``Agent.agents_md`` and ``agent_files`` rows have
 * different storage (column vs table) but surface identically:
 * ``AGENTS.md`` appears at the top of the tree as a "virtual" entry
 * that is always present and cannot be deleted — clearing its content
 * on Save writes ``agents_md=null``. Other rows live under the
 * ``skills/``, ``.codex/``, ``.claude/``, ``.gemini/``, ``.openhands/``
 * prefixes that the server whitelists in ``anygarden/agent_files.py``.
 *
 * Save semantics:
 *
 * - Saves happen in bulk on the Save button so the admin can edit
 *   several files without network churn.
 * - Path-based routing: the virtual ``AGENTS.md`` entry is flushed
 *   via ``updateAgent({agents_md_set: true})``; all others go
 *   through ``upsertAgentFile``.
 * - Changes take effect on the NEXT spawn, not immediately — the
 *   running subprocess is not hot-reloaded.
 *
 * Style: follows DESIGN.md (warm neutral palette, whisper borders,
 * near-black text, single-accent brand color).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  ChevronDown,
  ChevronRight,
  Download,
  FileText,
  FolderPlus,
  Plus,
  Trash2,
  Upload,
} from 'lucide-react'
import PresenceDot from '@/components/PresenceDot'
import { deriveAgentOnline } from '@/lib/agent-liveness'
import type { Agent, AgentFile, AttachedSkill, SkillPreview } from '@/hooks/useAgents'
import { BookOpen, ExternalLink, Lock } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

// Allowed top-level prefixes from the server-side whitelist.
// Must stay in sync with ``anygarden-server/anygarden/agent_files.py``.
const ALLOWED_PREFIXES: readonly string[] = [
  'skills/',
  '.codex/',
  '.claude/',
  '.gemini/',
  '.openhands/',
]

// Allowed file extensions from the server-side whitelist.
// Must stay in sync with ``_ALLOWED_EXTENSIONS`` in
// ``packages/cluster/anygarden/agent_files.py``. Used for the upload
// ``accept`` hint and for client-side pre-validation so the admin
// gets immediate feedback instead of a 400 from the server.
const ALLOWED_EXTENSIONS: readonly string[] = [
  '.md',
  '.json',
  '.toml',
  '.txt',
  '.yaml',
  '.yml',
  '.env',
  // Issue #112 — scripts admitted under ``skills/<name>/scripts/*``.
  // anygarden does not execute these; engine CLIs do.
  '.sh',
  '.py',
  '.js',
  '.ts',
  '.mjs',
]

// Friendly label for each prefix grouping in the file list.
const PREFIX_LABELS: Record<string, string> = {
  'skills/': 'Skills',
  '.codex/': 'Codex config',
  '.claude/': 'Claude Code config',
  '.gemini/': 'Gemini CLI config',
  '.openhands/': 'OpenHands config',
}

// Issue #112 — engine → permissible prefixes. An agent's ``engine``
// is fixed at creation, so only the matching CLI's config dir is
// meaningful; showing every other engine's config would just clutter
// the tree with dead paths. ``skills/`` and ``AGENTS.md`` are
// universal so every engine gets them.
//
// Must stay in sync with the backend's ``_ALLOWED_PREFIXES``:
// adding a new engine means editing this map AND
// ``anygarden/agent_files.py``. The server accepts every prefix
// regardless of engine today, so this filter is purely a UX
// affordance — API callers still bear the responsibility of not
// writing garbage.
const ENGINE_PREFIXES: Record<string, readonly string[]> = {
  'claude-code': ['skills/', '.claude/'],
  'codex-cli': ['skills/', '.codex/'],
  'gemini-cli': ['skills/', '.gemini/'],
  'openhands': ['skills/', '.openhands/'],
  // API-only and library-style engines: no CLI config dir to edit.
  'deep-agents': ['skills/'],
  'openai': ['skills/'],
  'anthropic': ['skills/'],
}

// Fallback for engines we haven't classified yet — surface only the
// universal ``skills/`` bucket rather than exposing every config dir
// by accident.
const FALLBACK_ENGINE_PREFIXES: readonly string[] = ['skills/']

function allowedPrefixesForEngine(engine: string | undefined): readonly string[] {
  if (!engine) return FALLBACK_ENGINE_PREFIXES
  return ENGINE_PREFIXES[engine] ?? FALLBACK_ENGINE_PREFIXES
}

// ── Tree structure (Issue #112) ───────────────────────────────────
//
// The file list used to be rendered as a flat "group by top-level
// prefix" stack. Real skills carry nested directories (``skills/
// <name>/scripts/``, ``skills/<name>/references/``) that the flat
// view could not express. ``buildTree`` produces a recursive
// ``TreeNode`` forest so arbitrary depth renders as a collapsible
// tree with the same click-to-select and dirty-dot affordances the
// flat list already had.

export type TreeNode =
  | {
      kind: 'file'
      /** Absolute path (``skills/coder/scripts/build.py``). */
      path: string
      /** Last segment only, rendered by the file row. */
      name: string
      file: WorkingFile
    }
  | {
      kind: 'dir'
      /** Absolute path (``skills/coder/scripts``). Used as the
       *  stable id for expand/collapse persistence. */
      path: string
      /** Last segment, shown as the directory label. */
      name: string
      children: TreeNode[]
    }

// Display order helpers: directories first, then files; each group
// sorted alphabetically. Keeps the tree stable even when rows are
// dirty-added mid-session.
function compareNodes(a: TreeNode, b: TreeNode): number {
  if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
  return a.name.localeCompare(b.name)
}

/**
 * Build a recursive tree from the flat working-copy file list.
 *
 * - Hidden / undeletable entries (``deleted``) are skipped.
 * - The virtual ``AGENTS.md`` row is placed at the root as a
 *   regular file node so it reads as "first among equals".
 * - File paths with a prefix outside ``allowedPrefixes`` are
 *   silently skipped. Together with the engine-based prefix
 *   filter at save time, that keeps dead files from older engines
 *   out of the tree (today ``engine`` is immutable so this is a
 *   defense-in-depth check, not a user-visible path).
 *
 * Complexity is O(N·D) where N is the file count and D the
 * average depth; with the 6-segment cap that's effectively linear.
 */
export function buildTree(
  files: readonly WorkingFile[],
  allowedPrefixes: readonly string[],
): TreeNode[] {
  const root: TreeNode[] = []
  // Directory lookup: absolute path → dir node. Lets us attach
  // children in one pass without re-scanning the forest each time.
  const dirByPath = new Map<string, Extract<TreeNode, { kind: 'dir' }>>()

  for (const f of files) {
    if (f.deleted) continue
    // Virtual rows (AGENTS.md) sit at the root. The engine-prefix
    // filter below would reject them because they have no prefix,
    // so short-circuit first.
    if (f.virtual) {
      root.push({ kind: 'file', path: f.path, name: f.path, file: f })
      continue
    }
    if (!allowedPrefixes.some(p => f.path.startsWith(p))) continue

    const parts = f.path.split('/')
    let parent: TreeNode[] = root
    // Walk every segment except the last — those become directory
    // nodes. The last segment becomes the file node.
    for (let i = 0; i < parts.length - 1; i++) {
      const dirPath = parts.slice(0, i + 1).join('/')
      const existing = dirByPath.get(dirPath)
      if (existing) {
        parent = existing.children
        continue
      }
      const dirNode: Extract<TreeNode, { kind: 'dir' }> = {
        kind: 'dir',
        path: dirPath,
        name: parts[i],
        children: [],
      }
      dirByPath.set(dirPath, dirNode)
      parent.push(dirNode)
      parent = dirNode.children
    }
    parent.push({
      kind: 'file',
      path: f.path,
      name: parts[parts.length - 1],
      file: f,
    })
  }

  // Recursive sort: dirs before files, each sorted by name.
  const sortRec = (nodes: TreeNode[]): void => {
    nodes.sort(compareNodes)
    for (const n of nodes) {
      if (n.kind === 'dir') sortRec(n.children)
    }
  }
  sortRec(root)
  return root
}

// Predicate used by the UI to decide where the "add file inside
// this skill" shortcut applies. A skill directory is a child of
// the top-level ``skills/`` group — ``skills/<name>`` with exactly
// two path segments. Nested subdirs (``skills/<name>/scripts``)
// don't get the shortcut: their files live beside the skill's
// ``SKILL.md`` and the "New file" path input already prefills
// correctly from the skill root.
export function isSkillDirNode(node: TreeNode): boolean {
  if (node.kind !== 'dir') return false
  return node.path.startsWith('skills/') && node.path.split('/').length === 2
}

// Friendly label for the top-level prefix dir nodes. For every
// other depth, the raw directory name is used.
function dirLabelFor(node: Extract<TreeNode, { kind: 'dir' }>, depth: number): string {
  if (depth === 0) {
    const key = `${node.name}/`  // PREFIX_LABELS keys carry the trailing slash
    return PREFIX_LABELS[key] ?? node.name
  }
  return node.name
}

// Slugify a human-entered skill name so the resulting directory
// name is safe for the agent_files path whitelist (``skills/<slug>/``).
// Keeps the alphabet narrow: lowercase alphanumerics and dashes.
export function slugifySkillName(raw: string): string {
  return raw
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// Starter content for a fresh ``SKILL.md``. Frontmatter mirrors the
// Anthropic/Agents.md convention so downstream CLIs can discover
// the skill by name and description.
function skillTemplate(slug: string): string {
  return `---\nname: ${slug}\ndescription: TODO — when should this skill activate?\n---\n\n# ${slug}\n\n<!-- TODO: describe the skill's purpose and usage. -->\n`
}

// ── Recursive tree renderer (Issue #112) ──────────────────────────
//
// A single recursive function that returns JSX for a node and (if
// expanded) its descendants. Kept at module scope so the component
// body stays legible; everything it needs is passed in.

interface RenderTreeNodeArgs {
  node: TreeNode
  depth: number
  selectedPath: string | null
  expandedPaths: Set<string>
  onSelect: (path: string) => void
  onToggle: (dirPath: string) => void
  onRemove: (path: string) => void
  onAddInSkill: (skillName: string) => void
}

function renderTreeNode(args: RenderTreeNodeArgs): ReactNode {
  const { node, depth, selectedPath, expandedPaths, onSelect, onToggle, onRemove, onAddInSkill } = args
  // Consistent indent per depth — dense enough to keep ~5 levels
  // visible in the 240px tree column. ``paddingLeft`` is inline so
  // the depth math doesn't have to live in Tailwind's class
  // vocabulary.
  const indentStyle: CSSProperties = { paddingLeft: 12 + depth * 12 }

  if (node.kind === 'file') {
    const f = node.file
    const isSelected = f.path === selectedPath
    const showTrash = !f.virtual
    return (
      <div
        key={f.path}
        className={`group flex items-center justify-between pr-3 py-1.5 text-sm cursor-pointer transition-colors ${
          isSelected
            ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]'
            : 'hover:bg-[var(--color-surface-alt)] text-[var(--color-foreground)]'
        }`}
        style={indentStyle}
        onClick={() => onSelect(f.path)}
        data-testid={`agent-edit-file-${f.path}`}
        data-virtual={f.virtual ? 'true' : undefined}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          {f.virtual ? (
            <FileText
              className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-muted)]"
              aria-hidden="true"
            />
          ) : null}
          <span className="truncate font-mono text-xs">
            {node.name}
            {f.dirty ? <span className="ml-1 opacity-70">•</span> : null}
          </span>
        </span>
        {showTrash ? (
          <button
            type="button"
            onClick={e => {
              e.stopPropagation()
              onRemove(f.path)
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity"
            title={`Remove ${f.path}`}
          >
            <Trash2 className="h-3.5 w-3.5 text-[var(--color-warning)]" />
          </button>
        ) : null}
      </div>
    )
  }

  // Directory node.
  const isOpen = expandedPaths.has(node.path)
  const isSkill = isSkillDirNode(node)
  const label = dirLabelFor(node, depth)
  const fileCount = countFilesRec(node)
  return (
    <div key={node.path}>
      <div
        className="group flex items-center justify-between pr-3 py-1 text-sm cursor-pointer hover:bg-[var(--color-surface-alt)] text-[var(--color-foreground)] transition-colors"
        style={indentStyle}
        onClick={() => onToggle(node.path)}
        data-testid={`agent-edit-dir-${node.path}`}
      >
        <span className="flex min-w-0 items-center gap-1">
          {isOpen ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          )}
          <span className={`truncate text-xs ${depth === 0 ? 'uppercase tracking-wider text-[var(--color-foreground-muted)]' : 'font-mono text-[var(--color-foreground)]'}`}>
            {label}
          </span>
          <span className="text-[10px] text-[var(--color-foreground-subtle)]">
            ({fileCount})
          </span>
        </span>
        {isSkill ? (
          <button
            type="button"
            onClick={e => {
              e.stopPropagation()
              onAddInSkill(node.name)
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity rounded p-0.5 hover:bg-black/5"
            title={`Add file in ${node.path}`}
            data-testid={`agent-edit-add-in-skill-${node.name}`}
          >
            <Plus className="h-3.5 w-3.5 text-[var(--color-foreground-muted)]" />
          </button>
        ) : null}
      </div>
      {isOpen &&
        node.children.map(child =>
          renderTreeNode({ ...args, node: child, depth: depth + 1 }),
        )}
    </div>
  )
}

// Count files recursively under a directory node, for the "(N)"
// badge shown next to dir labels.
function countFilesRec(node: TreeNode): number {
  if (node.kind === 'file') return 1
  let total = 0
  for (const c of node.children) total += countFilesRec(c)
  return total
}

// Strictly decode a ``File`` as UTF-8, throwing if the bytes are not
// valid UTF-8. Unlike ``FileReader.readAsText`` / ``Blob.text()`` —
// which silently replace invalid sequences with U+FFFD — this rejects
// binary files so they never reach the ``agent_files.content`` text
// column.
async function decodeUtf8Strict(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const decoder = new TextDecoder('utf-8', { fatal: true })
  return decoder.decode(buffer)
}

// Return the final path segment, e.g. ``SKILL.md`` for
// ``skills/greeting/SKILL.md``. Falls back to the full path when the
// input has no slash (defensive — every valid agent_files path has at
// least one segment under a prefix).
function basename(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx === -1 ? path : path.slice(idx + 1)
}

// Accept hint for the upload picker. Includes ``text/*`` so the
// system dialog is forgiving on files without the exact extension
// (e.g. dotfiles); client-side validation still enforces the
// whitelist before the working copy accepts the content.
const UPLOAD_ACCEPT = [...ALLOWED_EXTENSIONS, 'text/*'].join(',')

// The virtual ``AGENTS.md`` entry is identified by exact path match.
// Keeping the constant named lets search / refactor tools flag every
// touchpoint instead of leaving string literals scattered across the
// file. Path has no prefix because the materializer writes it at the
// agent root (``agent_root/AGENTS.md``) — distinct from every other
// allowed prefix in ``_ALLOWED_PREFIXES``.
const AGENTS_MD_PATH = 'AGENTS.md'

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
  // Issue #109 — ``true`` for the virtual ``AGENTS.md`` row. Virtual
  // rows are always present in the tree, cannot be deleted via the
  // trash icon, cannot be created through the "New file" form, and
  // route through ``updateAgent`` on Save (not ``upsertAgentFile``).
  virtual?: boolean
}

// Build the virtual AGENTS.md working-file row from an agent prop.
// ``originalContent`` mirrors the server: ``null`` when the agent
// has never had an AGENTS.md (distinct from the empty string, which
// is a valid saved value).
function makeAgentsMdFile(md: string | null | undefined, updatedAt: string): WorkingFile {
  const content = md ?? ''
  return {
    path: AGENTS_MD_PATH,
    content,
    updated_at: updatedAt,
    originalContent: md ?? null,
    dirty: false,
    deleted: false,
    virtual: true,
  }
}

interface Props {
  agent: Agent | null
  fetchAgentFiles: (id: string) => Promise<AgentFile[]>
  updateAgent: (
    id: string,
    patch: { name?: string; agents_md?: string | null; agents_md_set?: boolean },
  ) => Promise<Agent>
  upsertAgentFile: (id: string, path: string, content: string) => Promise<AgentFile>
  deleteAgentFile: (id: string, path: string) => Promise<void>
  // Issue #133 — optional read-only surfacing of library skills
  // attached to this agent. Both default to "no skills shown"
  // when omitted so older callers / tests remain compatible.
  fetchAttachedSkills?: (id: string) => Promise<AttachedSkill[]>
  fetchSkillPreview?: (skillId: string) => Promise<SkillPreview | null>
  /** Fired when the admin clicks "View in Skills" on an attached
   *  skill row — the parent closes the settings dialog before
   *  navigating. */
  onNavigateAway?: () => void
}

export default function ManifestPanel({
  agent,
  fetchAgentFiles,
  updateAgent,
  upsertAgentFile,
  deleteAgentFile,
  fetchAttachedSkills,
  fetchSkillPreview,
  onNavigateAway,
}: Props) {
  const navigate = useNavigate()
  const [files, setFiles] = useState<WorkingFile[]>([])
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showNewFileForm, setShowNewFileForm] = useState(false)
  const [newFilePath, setNewFilePath] = useState('skills/')

  // Non-null when the admin has picked a file from the upload picker
  // but has not yet confirmed the path. The same "New file" form is
  // reused for confirmation so ``handleAddFile`` can consume this on
  // commit. A null value means the form is in manual "create empty
  // file" mode (the original behavior).
  const [pendingContent, setPendingContent] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Issue #112 — "New skill" form is mutually exclusive with the
  // "New file" form. The skill flow needs only a name; the path
  // and frontmatter template are synthesized on submit.
  const [showNewSkillForm, setShowNewSkillForm] = useState(false)
  const [newSkillName, setNewSkillName] = useState('')

  // Issue #112 — recursive tree expand/collapse persistence. Each
  // agent gets its own localStorage key so unrelated agents don't
  // leak expand state into each other. The value is a Set of
  // directory paths (no trailing slash, matching ``TreeNode.path``).
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set())

  // Issue #133 — attached library skills surfaced as a read-only
  // section. ``previewBySkillId`` is populated lazily on selection
  // so opening the dialog for an agent with many skills doesn't
  // fan out N preview requests up front. The selection state is
  // mutually exclusive with ``selectedPath`` — clicking a library
  // skill file clears ``selectedPath`` and vice versa.
  const [attachedSkills, setAttachedSkills] = useState<AttachedSkill[]>([])
  const [previewBySkillId, setPreviewBySkillId] = useState<Record<string, SkillPreview>>({})
  const [selectedAttachedSkillId, setSelectedAttachedSkillId] = useState<string | null>(null)
  const [attachedSkillSection, setAttachedSkillSection] = useState(true)

  // Pull the canonical file list from the server into the working
  // copy, and prepend the virtual ``AGENTS.md`` row.
  //
  // Used in two places with slightly different behavior:
  //
  // - ``loadInitial`` (on open): seeds AGENTS.md from the
  //   parent-supplied ``agent`` prop, because that's the freshest
  //   the dialog has access to at open time.
  //
  // - ``resyncAfterSave`` (after Save succeeds): seeds AGENTS.md
  //   from the local working copy that was just flushed — the
  //   ``agent`` prop is a STALE snapshot from when the dialog
  //   was opened, so re-reading it would clobber the edit we
  //   just saved. For file rows we rely on the server-fresh
  //   ``fetchAgentFiles`` result, which already reflects any
  //   upserts/deletes we just issued.
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
      const [working, skills] = await Promise.all([
        fetchFilesIntoWorking(agent.id),
        fetchAttachedSkills ? fetchAttachedSkills(agent.id) : Promise.resolve([]),
      ])
      const agentsMdFile = makeAgentsMdFile(agent.agents_md, new Date().toISOString())
      const allFiles = [agentsMdFile, ...working]
      setFiles(allFiles)
      setAttachedSkills(skills)
      // Reset preview cache when the dialog reloads for a different
      // agent so we don't display stale bodies.
      setPreviewBySkillId({})
      setSelectedAttachedSkillId(null)
      setSelectedPath(prev => {
        // Keep the previously-selected path if it still exists,
        // otherwise default to AGENTS.md so the agent's "identity"
        // is the first thing the admin sees when the dialog opens.
        if (prev && allFiles.some(f => f.path === prev)) return prev
        return AGENTS_MD_PATH
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [agent, fetchFilesIntoWorking, fetchAttachedSkills])

  const resyncAfterSave = useCallback(async () => {
    if (!agent) return
    try {
      const serverRows = await fetchFilesIntoWorking(agent.id)
      // Preserve the just-saved AGENTS.md content rather than
      // re-reading the stale ``agent.agents_md`` prop. After Save
      // succeeds, the working copy's content is already server-
      // authoritative; we just need to promote ``content`` to
      // ``originalContent`` and drop the dirty flag.
      setFiles(prev => {
        const oldAgentsMd = prev.find(f => f.virtual && f.path === AGENTS_MD_PATH)
        const savedContent = oldAgentsMd?.content ?? ''
        const refreshed: WorkingFile = {
          path: AGENTS_MD_PATH,
          content: savedContent,
          updated_at: new Date().toISOString(),
          // Empty string on the client represents ``agents_md=null``
          // on the server (see the Save branch), so mirror that here.
          originalContent: savedContent === '' ? null : savedContent,
          dirty: false,
          deleted: false,
          virtual: true,
        }
        return [refreshed, ...serverRows]
      })
      setSelectedPath(prev => {
        if (prev && (prev === AGENTS_MD_PATH || serverRows.some(f => f.path === prev))) return prev
        return AGENTS_MD_PATH
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [agent, fetchFilesIntoWorking])

  // Issue #479 — seed the editor working copy only when the dialog opens or
  // the selected agent's STABLE id changes, never on every refreshed ``agent``
  // object. The parent (#281 pattern) derives a fresh Agent object from the
  // live list on every ``useAgents.fetchAgents`` — and the #219 transitional
  // poll fires that every 1.5s — so an unconditional re-seed clobbered any
  // in-progress AGENTS.md/file edit. Mirrors the ``agent?.id``-gated
  // ``expandedPaths`` effect below. Re-opening for the same agent is covered
  // by the dialog unmounting this panel on close (a fresh mount re-seeds).
  const seededAgentIdRef = useRef<string | null>(null)
  useEffect(() => {
    if (!agent) {
      seededAgentIdRef.current = null
      return
    }
    if (seededAgentIdRef.current === agent.id) return
    seededAgentIdRef.current = agent.id
    void loadInitial()
  }, [agent, loadInitial])

  // Issue #112 — engine-based prefix filter. Changes to the tree
  // render in response to ``agent?.engine``; tests assert that a
  // claude-code agent never sees a ``.codex/`` node.
  const engineAllowedPrefixes = useMemo(
    () => allowedPrefixesForEngine(agent?.engine),
    [agent?.engine],
  )

  // Seed expanded paths from localStorage (per-agent key) or, on
  // first open of an agent, default to expanded top-level prefix
  // dirs so the tree shows something useful immediately. We read
  // here instead of in the ``useState`` initializer because
  // ``agent`` may be null on the first render and changes across
  // dialog opens.
  useEffect(() => {
    if (!agent) return
    const key = `anygarden_agent_tree_${agent.id}`
    try {
      const saved = localStorage.getItem(key)
      if (saved) {
        setExpandedPaths(new Set<string>(JSON.parse(saved)))
        return
      }
    } catch {
      // Storage unavailable (private mode / SSR) — fall through to
      // the seed so the tree still renders something sensible.
    }
    const seed = new Set<string>()
    for (const p of engineAllowedPrefixes) {
      // TreeNode.path uses ``parts.join('/')`` with no trailing
      // slash, so strip the prefix's trailing slash for the seed.
      seed.add(p.replace(/\/$/, ''))
    }
    setExpandedPaths(seed)
  }, [agent?.id, engineAllowedPrefixes])

  // Toggle a directory node's expand/collapse state and persist.
  // Persist failures are swallowed (same rationale as the seed
  // effect: localStorage is best-effort).
  const toggleExpanded = useCallback((dirPath: string) => {
    setExpandedPaths(prev => {
      const next = new Set(prev)
      if (next.has(dirPath)) next.delete(dirPath)
      else next.add(dirPath)
      if (agent) {
        try {
          localStorage.setItem(
            `anygarden_agent_tree_${agent.id}`,
            JSON.stringify(Array.from(next)),
          )
        } catch {
          // ignore
        }
      }
      return next
    })
  }, [agent])

  // The recursive forest that backs the file tree render. Virtual
  // AGENTS.md sits at the root; the engine-allowed prefixes each
  // form a top-level directory (``skills/``, ``.claude/``, etc.)
  // with arbitrary-depth subdirectories beneath them.
  const treeRoots = useMemo(
    () => buildTree(files, engineAllowedPrefixes),
    [files, engineAllowedPrefixes],
  )

  const selectedFile = useMemo(
    () => (selectedPath ? files.find(f => f.path === selectedPath) ?? null : null),
    [files, selectedPath],
  )

  // Issue #133 — the attached-skill file currently selected, if any.
  // Mutually exclusive with ``selectedFile``; the render branch
  // below picks whichever one is non-null.
  const selectedAttachedSkill = useMemo(
    () => attachedSkills.find(s => s.id === selectedAttachedSkillId) ?? null,
    [attachedSkills, selectedAttachedSkillId],
  )
  const selectedAttachedPreview = selectedAttachedSkillId
    ? previewBySkillId[selectedAttachedSkillId] ?? null
    : null

  // Lazy-load the SKILL.md body on first selection. The preview
  // endpoint also returns ``extra_files`` paths so we can render
  // them as a non-interactive list alongside the body.
  const handleSelectAttachedSkill = useCallback(
    async (skillId: string) => {
      setSelectedAttachedSkillId(skillId)
      setSelectedPath(null)
      if (previewBySkillId[skillId] || !fetchSkillPreview) return
      const pr = await fetchSkillPreview(skillId)
      if (pr) setPreviewBySkillId(prev => ({ ...prev, [skillId]: pr }))
    },
    [previewBySkillId, fetchSkillPreview],
  )

  // Clicking a WorkingFile in the regular tree clears any
  // attached-skill selection so the editor shows only one file.
  const handleSelectWorkingPath = useCallback((path: string) => {
    setSelectedPath(path)
    setSelectedAttachedSkillId(null)
  }, [])

  // Track "anything changed" to enable/disable the Save button and
  // warn on close. AGENTS.md sits in ``files`` as a virtual row so
  // its ``dirty`` flag is already covered by the scan below.
  const hasChanges = useMemo(
    () => files.some(f => f.dirty || f.deleted),
    [files],
  )

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
    // AGENTS.md is a virtual entry that always lives at the top of
    // the tree; it cannot be "created" through this form because it
    // already exists and has a distinct save path. Reject explicitly
    // so the admin sees a clear message instead of a confusing
    // "file already exists" or prefix-validation error.
    if (path === AGENTS_MD_PATH) {
      setError(`${AGENTS_MD_PATH} already exists at the top of the tree`)
      return
    }
    // Client-side validation mirrors a slice of the server-side
    // whitelist so the admin gets immediate feedback. The server
    // has the authoritative check on save. Path-structure rules
    // (length, segments, control chars) are left to the server —
    // mirroring the whole validator here would be redundant.
    //
    // Issue #112 — the admissible prefix set is narrowed by engine
    // so claude-code admins can't accidentally drop a file under
    // ``.codex/`` (which that engine will never read). The server
    // still accepts any whitelist-prefix path regardless of engine,
    // so this check is a UX affordance.
    const engineAllowed = allowedPrefixesForEngine(agent?.engine)
    if (!engineAllowed.some(p => path.startsWith(p))) {
      setError(`path must start with one of: ${engineAllowed.join(', ')}`)
      return
    }
    if (!ALLOWED_EXTENSIONS.some(ext => path.endsWith(ext))) {
      setError(`extension must be one of: ${ALLOWED_EXTENSIONS.join(', ')}`)
      return
    }
    const uploadContent = pendingContent
    const isUpload = uploadContent !== null
    const existsVisible = files.some(f => f.path === path && !f.deleted)
    // Manual "New file" rejects duplicates to match the original
    // behavior. Upload is explicit content, so we offer to overwrite.
    if (existsVisible && !isUpload) {
      setError(`file ${path} already exists`)
      return
    }
    if (existsVisible && isUpload) {
      const ok = window.confirm(`Overwrite existing ${path}?`)
      if (!ok) return
    }

    if (isUpload) {
      // Upload always rewrites content and un-deletes if tombstoned,
      // because the admin just picked a concrete file to land there.
      setFiles(prev => {
        const hit = prev.some(f => f.path === path)
        if (hit) {
          return prev.map(f =>
            f.path === path
              ? {
                  ...f,
                  content: uploadContent,
                  dirty: f.originalContent !== uploadContent,
                  deleted: false,
                }
              : f,
          )
        }
        return [
          ...prev,
          {
            path,
            content: uploadContent,
            updated_at: new Date().toISOString(),
            originalContent: null,
            dirty: true,
            deleted: false,
          },
        ]
      })
    } else {
      // Manual add: un-delete a tombstoned row (preserving its
      // original content so an accidental delete+re-add is a no-op)
      // or create a fresh empty row.
      const restored = files.find(f => f.path === path && f.deleted)
      if (restored) {
        setFiles(prev =>
          prev.map(f =>
            f.path === path
              ? { ...f, deleted: false, dirty: f.content !== f.originalContent }
              : f,
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
    }

    setSelectedPath(path)
    setShowNewFileForm(false)
    setNewFilePath('skills/')
    setPendingContent(null)
    setError(null)
  }

  const handleCancelNewFile = () => {
    setShowNewFileForm(false)
    setNewFilePath('skills/')
    setPendingContent(null)
  }

  // Trigger the hidden ``<input type="file">`` to open the OS picker.
  // Uploads are explicit (no drag&drop for now — out of scope).
  const handleUploadClick = () => {
    setError(null)
    fileInputRef.current?.click()
  }

  // Issue #112 — "+" button on a skill dir node. Prefills the
  // "New file" form with the skill's path + trailing slash so the
  // admin only types the filename. Closes the "New skill" form if
  // it was open so the two flows don't compete for the same input.
  const handleAddInSkill = useCallback((skillName: string) => {
    setError(null)
    setShowNewSkillForm(false)
    setNewSkillName('')
    setShowNewFileForm(true)
    setNewFilePath(`skills/${skillName}/`)
    // Keep the skill expanded so the new file appears in place after
    // commit. Persist via ``toggleExpanded`` only on actual toggles;
    // here we unconditionally ensure expanded.
    setExpandedPaths(prev => {
      const dirPath = `skills/${skillName}`
      if (prev.has(dirPath)) return prev
      const next = new Set(prev)
      next.add(dirPath)
      if (agent) {
        try {
          localStorage.setItem(
            `anygarden_agent_tree_${agent.id}`,
            JSON.stringify(Array.from(next)),
          )
        } catch {
          // ignore
        }
      }
      return next
    })
  }, [agent])

  // Issue #112 — "New skill" action. Takes a human-readable name
  // (e.g. "Code Review"), slugifies it, and creates
  // ``skills/<slug>/SKILL.md`` with a frontmatter template in the
  // working copy. The admin can then edit SKILL.md on the right
  // and Save along with any other changes in one pass.
  const handleCreateSkill = () => {
    const slug = slugifySkillName(newSkillName)
    if (!slug) {
      setError('skill name must contain at least one alphanumeric character')
      return
    }
    const path = `skills/${slug}/SKILL.md`
    if (files.some(f => f.path === path && !f.deleted)) {
      setError(`skill "${slug}" already exists`)
      return
    }
    setFiles(prev => [
      ...prev,
      {
        path,
        content: skillTemplate(slug),
        updated_at: new Date().toISOString(),
        originalContent: null,
        dirty: true,
        deleted: false,
      },
    ])
    // Expand the new skill dir so the file appears immediately.
    setExpandedPaths(prev => {
      const next = new Set(prev)
      next.add('skills')
      next.add(`skills/${slug}`)
      if (agent) {
        try {
          localStorage.setItem(
            `anygarden_agent_tree_${agent.id}`,
            JSON.stringify(Array.from(next)),
          )
        } catch {
          // ignore
        }
      }
      return next
    })
    setSelectedPath(path)
    setShowNewSkillForm(false)
    setNewSkillName('')
    setError(null)
  }

  const handleCancelNewSkill = () => {
    setShowNewSkillForm(false)
    setNewSkillName('')
  }

  // Read the picked file as strict UTF-8 and stage it for path
  // confirmation. The admin edits ``newFilePath`` in the existing
  // "New file" form row, then clicks Add to commit to the working
  // copy.
  const handleUploadChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset so picking the same file twice still fires ``change``.
    e.target.value = ''
    if (!file) return
    setError(null)
    let content: string
    try {
      content = await decodeUtf8Strict(file)
    } catch {
      setError(`${file.name}: not a valid UTF-8 text file (binary is not supported)`)
      return
    }
    // Default path lands under ``skills/`` because that's the most
    // common upload target (admins rarely upload engine configs).
    // The admin can retarget to any allowed prefix before committing.
    setPendingContent(content)
    setNewFilePath(`skills/${file.name}`)
    setShowNewFileForm(true)
  }

  // Push the currently-selected file's working-copy content to the
  // admin's machine as a text download. Uses the working copy, not
  // ``originalContent``, so unsaved edits are included — the admin
  // is treating the editor as the source of truth.
  const handleDownload = () => {
    if (!selectedFile) return
    const blob = new Blob([selectedFile.content], {
      type: 'text/plain;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = basename(selectedFile.path)
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleRemoveFile = (path: string) => {
    // Virtual rows (currently just AGENTS.md) can't be deleted —
    // the trash icon is hidden in the tree for them, but gate this
    // callback too as a belt-and-braces guard in case a future
    // caller wires it up elsewhere.
    const target = files.find(f => f.path === path)
    if (target?.virtual) return
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
      // 1. New and updated rows — path-based routing between the
      //    ``agents_md`` column (virtual AGENTS.md row) and the
      //    ``agent_files`` table (everything else). A single bad
      //    path 400s that one call; we surface the error and stop
      //    so the admin can fix it without losing state on the
      //    other rows.
      for (const f of files) {
        if (f.deleted) continue
        if (!f.dirty) continue
        if (f.virtual && f.path === AGENTS_MD_PATH) {
          // Empty string is a valid "admin cleared all rules"
          // state on the client but the server stores that as
          // ``null`` (the column is nullable). Keep the dirty
          // flag as the source of truth — we only land here when
          // the content changed from ``originalContent``.
          await updateAgent(agent.id, {
            agents_md: f.content === '' ? null : f.content,
            agents_md_set: true,
          })
        } else {
          await upsertAgentFile(agent.id, f.path, f.content)
        }
      }

      // 2. Deletions — only for server-backed files (have an
      //    ``originalContent``) that are not virtual. Admin-created
      //    files that were then marked deleted never hit the server
      //    in the first place, so skipping them is correct. Virtual
      //    rows can't enter the deleted state (guarded in
      //    ``handleRemoveFile``) but the ``!f.virtual`` check keeps
      //    the save loop self-contained.
      for (const f of files) {
        if (f.virtual) continue
        if (f.deleted && f.originalContent !== null) {
          await deleteAgentFile(agent.id, f.path)
        }
      }

      // Soft resync after save: pull the fresh ``agent_files``
      // rows so ``updated_at`` values reflect the write, drop
      // rows that were marked ``deleted`` in the working copy,
      // and clear dirty flags. Deliberately does NOT re-read
      // ``agents_md`` via the ``agent`` prop — that's a stale
      // snapshot; the working-copy content we just saved is
      // authoritative.
      await resyncAfterSave()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setSaving(false)
  }

  return (
    <div className="flex h-full flex-col" data-testid="manifest-panel">
      <p className="text-caption text-[var(--color-foreground-muted)] pb-2">
        Update the agent's system prompt and on-disk files. Changes take
        effect on the next spawn — restart the agent to apply.
      </p>
      {loading ? (
          <div className="py-8 text-center text-caption text-[var(--color-foreground-muted)]">
            Loading…
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto py-2 space-y-5">
            {/* Files tree + editor ----------------------------------- */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Files</Label>
                <div className="flex items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleUploadClick}
                    data-testid="agent-edit-upload"
                  >
                    <Upload className="mr-1 h-4 w-4" />
                    Upload
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      // Toggling out of the form should also clear any
                      // staged upload so the next open starts clean.
                      if (showNewFileForm) {
                        handleCancelNewFile()
                      } else {
                        handleCancelNewSkill()
                        setShowNewFileForm(true)
                      }
                    }}
                    data-testid="agent-edit-toggle-new-file"
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    New file
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      if (showNewSkillForm) {
                        handleCancelNewSkill()
                      } else {
                        handleCancelNewFile()
                        setShowNewSkillForm(true)
                      }
                    }}
                    data-testid="agent-edit-toggle-new-skill"
                  >
                    <FolderPlus className="mr-1 h-4 w-4" />
                    New skill
                  </Button>
                </div>
              </div>

              {/* Hidden upload input — triggered via the Upload button. */}
              <input
                ref={fileInputRef}
                type="file"
                accept={UPLOAD_ACCEPT}
                onChange={handleUploadChange}
                className="hidden"
                data-testid="agent-edit-upload-input"
              />

              {showNewFileForm ? (
                <div className="flex gap-2 items-center bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
                  {pendingContent !== null ? (
                    <Badge
                      variant="outline"
                      className="bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)] border-[color:color-mix(in_srgb,var(--color-brand)_20%,transparent)] shrink-0"
                      data-testid="agent-edit-upload-badge"
                    >
                      Upload
                    </Badge>
                  ) : null}
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
                  <Button size="sm" variant="ghost" onClick={handleCancelNewFile}>
                    Cancel
                  </Button>
                </div>
              ) : null}

              {showNewSkillForm ? (
                <div className="flex gap-2 items-center bg-[var(--color-surface-alt)] rounded-[var(--radius-md)] border border-[var(--color-border)] p-3">
                  <span className="shrink-0 font-mono text-xs text-[var(--color-foreground-muted)]">
                    skills/
                  </span>
                  <Input
                    value={newSkillName}
                    onChange={e => setNewSkillName(e.target.value)}
                    placeholder="greeting"
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleCreateSkill()
                      }
                    }}
                    autoFocus
                    data-testid="agent-edit-new-skill-name"
                  />
                  <span className="shrink-0 font-mono text-xs text-[var(--color-foreground-muted)]">
                    /SKILL.md
                  </span>
                  <Button size="sm" onClick={handleCreateSkill} data-testid="agent-edit-create-skill">
                    Create
                  </Button>
                  <Button size="sm" variant="ghost" onClick={handleCancelNewSkill}>
                    Cancel
                  </Button>
                </div>
              ) : null}

              <div className="grid grid-cols-[240px_1fr] gap-3 min-h-[280px]">
                {/* Left: file tree */}
                <div className="overflow-y-auto rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-background)]">
                  {treeRoots.length === 0 ? (
                    <div className="p-4 text-caption text-[var(--color-foreground-subtle)]">
                      No files yet. Click "New file" to add one.
                    </div>
                  ) : (
                    <div className="py-1">
                      {treeRoots.map(node =>
                        renderTreeNode({
                          node,
                          depth: 0,
                          selectedPath,
                          expandedPaths,
                          onSelect: handleSelectWorkingPath,
                          onToggle: toggleExpanded,
                          onRemove: handleRemoveFile,
                          onAddInSkill: handleAddInSkill,
                        }),
                      )}
                      {/* Issue #133 — read-only section for library
                          skills attached via the Skills admin page.
                          Rendered below the editable tree so it's
                          visually separated from files the admin
                          can modify. */}
                      {attachedSkills.length > 0 ? (
                        <div className="border-t border-[var(--color-border)] mt-1 pt-1">
                          <button
                            type="button"
                            onClick={() => setAttachedSkillSection(v => !v)}
                            className="w-full flex items-center gap-1.5 pr-3 py-1 text-[10px] uppercase tracking-wider text-[var(--color-foreground-muted)] hover:bg-[var(--color-surface-alt)]"
                            style={{ paddingLeft: 12 }}
                            data-testid="agent-edit-attached-skills-toggle"
                          >
                            {attachedSkillSection ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            )}
                            <BookOpen className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                            <span>Attached skills</span>
                            <span className="text-[10px] text-[var(--color-foreground-subtle)] normal-case tracking-normal">
                              ({attachedSkills.length})
                            </span>
                            <Lock className="ml-auto h-3 w-3 shrink-0 opacity-60" aria-hidden="true" />
                          </button>
                          {attachedSkillSection
                            ? attachedSkills.map(skill => {
                                const isSelected = selectedAttachedSkillId === skill.id
                                return (
                                  <button
                                    key={skill.id}
                                    type="button"
                                    onClick={() => void handleSelectAttachedSkill(skill.id)}
                                    className={`w-full flex items-center gap-1.5 pr-3 py-1.5 text-sm text-left transition-colors ${
                                      isSelected
                                        ? 'bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]'
                                        : 'hover:bg-[var(--color-surface-alt)] text-[var(--color-foreground)]'
                                    }`}
                                    style={{ paddingLeft: 24 }}
                                    data-testid={`agent-edit-attached-skill-${skill.name}`}
                                  >
                                    <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
                                    <span className="truncate font-mono text-xs">
                                      {skill.name}
                                    </span>
                                  </button>
                                )
                              })
                            : null}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>

                {/* Right: file content editor */}
                <div className="flex flex-col">
                  {selectedFile ? (
                    <>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <div className="font-mono text-xs text-[var(--color-foreground-muted)] truncate">
                          {selectedFile.path}
                        </div>
                        <button
                          type="button"
                          onClick={handleDownload}
                          className="inline-flex items-center gap-1 text-caption text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)] transition-colors"
                          title={`Download ${basename(selectedFile.path)}`}
                          data-testid="agent-edit-download"
                        >
                          <Download className="h-3.5 w-3.5" />
                          Download
                        </button>
                      </div>
                      <textarea
                        className="font-mono text-sm flex-1 w-full rounded-[var(--radius-xs)] border border-[var(--color-border-strong)] bg-[var(--color-background)] px-3 py-2 text-[var(--color-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)]"
                        value={selectedFile.content}
                        onChange={e => handleFileContentChange(e.target.value)}
                        spellCheck={false}
                        placeholder={
                          selectedFile.virtual
                            ? '# Agent role and rules\n\nDefine the agent\'s role, instructions, and any skill usage conventions here.'
                            : undefined
                        }
                        data-testid="agent-edit-file-content"
                      />
                    </>
                  ) : selectedAttachedSkill ? (
                    <>
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0">
                          <Lock className="h-3.5 w-3.5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
                          <div className="font-mono text-xs text-[var(--color-foreground-muted)] truncate">
                            skills/{selectedAttachedSkill.name}/SKILL.md
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            onNavigateAway?.()
                            navigate('/admin/skills')
                          }}
                          className="inline-flex items-center gap-1 text-caption text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)] transition-colors"
                          data-testid="agent-edit-view-in-skills"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                          View in Skills
                        </button>
                      </div>
                      <div className="mb-2 text-[11px] text-[var(--color-foreground-muted)] bg-[var(--color-surface-alt)] border border-[var(--color-border)] rounded-[var(--radius-xs)] px-2 py-1">
                        Managed by the Skills library — edit via the Skills admin page.
                      </div>
                      {selectedAttachedPreview ? (
                        <>
                          <textarea
                            className="font-mono text-sm flex-1 w-full rounded-[var(--radius-xs)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] px-3 py-2 text-[var(--color-foreground)] focus-visible:outline-none"
                            value={selectedAttachedPreview.skill_md}
                            readOnly
                            spellCheck={false}
                            data-testid="agent-edit-attached-skill-content"
                          />
                          {selectedAttachedPreview.extra_files.length > 0 ? (
                            <div className="mt-2">
                              <div className="text-[10px] uppercase tracking-wider text-[var(--color-foreground-muted)] mb-1">
                                Extra files ({selectedAttachedPreview.extra_files.length})
                              </div>
                              <ul className="rounded-[var(--radius-xs)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-2 text-xs max-h-28 max-w-full overflow-auto">
                                {selectedAttachedPreview.extra_files.map(p => (
                                  <li key={p} className="font-mono text-[var(--color-foreground-muted)]">
                                    {p}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <div
                          className="flex-1 flex items-center justify-center text-caption text-[var(--color-foreground-subtle)] border border-[var(--color-border)] rounded-[var(--radius-xs)]"
                          data-testid="agent-edit-attached-skill-loading"
                        >
                          Loading…
                        </div>
                      )}
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

      <div className="flex items-center justify-end gap-2 pt-3">
        <Button
          onClick={handleSave}
          disabled={!hasChanges || saving || loading}
          data-testid="agent-edit-save"
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>
    </div>
  )
}
