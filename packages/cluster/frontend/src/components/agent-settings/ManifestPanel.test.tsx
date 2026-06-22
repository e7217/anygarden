// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import ManifestPanel, {
  buildTree,
  isSkillDirNode,
  slugifySkillName,
  type TreeNode,
} from './ManifestPanel'
import type { Agent, AgentFile, AttachedSkill, SkillPreview } from '@/hooks/useAgents'

// jsdom doesn't implement the Object URL API, so stub them as
// callable no-ops before the download test spies on them.
beforeAll(() => {
  const u = URL as unknown as {
    createObjectURL: (b: Blob) => string
    revokeObjectURL: (url: string) => void
  }
  if (typeof u.createObjectURL !== 'function') u.createObjectURL = () => ''
  if (typeof u.revokeObjectURL !== 'function') u.revokeObjectURL = () => {}
})

afterEach(() => cleanup())

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'a1',
    name: 'bot',
    engine: 'claude-code',
    desired_state: 'running',
    actual_state: 'online',
    restart_policy: 'always',
    agents_md: null,
    ...overrides,
  }
}

function renderDialog(
  initialFiles: AgentFile[] = [],
  opts: {
    attachedSkills?: AttachedSkill[]
    skillPreview?: SkillPreview | null
  } = {},
) {
  const fetchAgentFiles = vi.fn().mockResolvedValue(initialFiles)
  const upsertAgentFile = vi
    .fn()
    .mockImplementation(async (_id: string, path: string, content: string) => ({
      path,
      content,
      updated_at: '2026-04-18T00:00:00Z',
    }))
  const deleteAgentFile = vi.fn().mockResolvedValue(undefined)
  const updateAgent = vi.fn().mockResolvedValue(makeAgent())
  const fetchAttachedSkills = vi
    .fn()
    .mockResolvedValue(opts.attachedSkills ?? [])
  const fetchSkillPreview = vi
    .fn()
    .mockResolvedValue(opts.skillPreview ?? null)
  render(
    <MemoryRouter>
      <ManifestPanel
        agent={makeAgent()}
        fetchAgentFiles={fetchAgentFiles}
        updateAgent={updateAgent}
        upsertAgentFile={upsertAgentFile}
        deleteAgentFile={deleteAgentFile}
        fetchAttachedSkills={fetchAttachedSkills}
        fetchSkillPreview={fetchSkillPreview}
      />
    </MemoryRouter>,
  )
  return {
    fetchAgentFiles,
    upsertAgentFile,
    deleteAgentFile,
    updateAgent,
    fetchAttachedSkills,
    fetchSkillPreview,
  }
}

describe('ManifestPanel — upload/download', () => {
  it('stages a UTF-8 file via the upload picker and fills the new-file form', async () => {
    renderDialog()
    // Dialog content mounts behind an async loadInitial; wait for
    // the Upload button before interacting.
    await screen.findByTestId('agent-edit-upload')

    const input = screen.getByTestId(
      'agent-edit-upload-input',
    ) as HTMLInputElement
    const file = new File(['hello world'], 'greet.md', {
      type: 'text/markdown',
    })
    fireEvent.change(input, { target: { files: [file] } })

    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    expect(pathInput.value).toBe('skills/greet.md')
    expect(screen.getByTestId('agent-edit-upload-badge')).toBeInTheDocument()
  })

  it('rejects a binary (non-UTF-8) file with an error', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-upload')

    const input = screen.getByTestId(
      'agent-edit-upload-input',
    ) as HTMLInputElement
    // Lone 0xff / 0xfe bytes — invalid UTF-8.
    const file = new File([new Uint8Array([0xff, 0xfe, 0x00, 0x80])], 'data.bin', {
      type: 'application/octet-stream',
    })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() =>
      expect(
        screen.getByText(/binary is not supported/i),
      ).toBeInTheDocument(),
    )
    // The confirmation form should not have opened because the
    // decode failed before setPendingContent/setShowNewFileForm.
    expect(screen.queryByTestId('agent-edit-new-file-path')).toBeNull()
  })

  it('rejects Add when the path extension is not in the server whitelist', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-upload')

    fireEvent.click(screen.getByTestId('agent-edit-toggle-new-file'))
    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    // Issue #112 — ``.sh`` is now whitelisted. Use a truly-rejected
    // extension instead (``.bash`` was deliberately left out of the
    // expansion).
    fireEvent.change(pathInput, { target: { value: 'skills/do.bash' } })
    fireEvent.click(screen.getByText('Add'))

    expect(
      await screen.findByText(/extension must be one of/i),
    ).toBeInTheDocument()
  })

  it('Download builds a blob URL from the selected file content', async () => {
    renderDialog([
      {
        path: 'skills/greet/SKILL.md',
        content: 'hello',
        updated_at: '2026-04-18T00:00:00Z',
      },
    ])
    // Issue #112 — the tree is recursive, so ``skills/greet`` sits
    // inside the (seeded-open) ``skills`` dir and itself needs to
    // be expanded before the file row renders. Click the dir
    // header to toggle it open.
    fireEvent.click(await screen.findByTestId('agent-edit-dir-skills/greet'))
    // Then select the file (AGENTS.md is the default selection, so
    // explicitly click the skill file for download to target it).
    const row = await screen.findByTestId('agent-edit-file-skills/greet/SKILL.md')
    fireEvent.click(row)

    const createSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob:test')
    const revokeSpy = vi
      .spyOn(URL, 'revokeObjectURL')
      .mockImplementation(() => {})

    fireEvent.click(screen.getByTestId('agent-edit-download'))

    expect(createSpy).toHaveBeenCalledTimes(1)
    const blobArg = createSpy.mock.calls[0][0] as Blob
    await expect(blobArg.text()).resolves.toBe('hello')
    expect(revokeSpy).toHaveBeenCalledWith('blob:test')

    createSpy.mockRestore()
    revokeSpy.mockRestore()
  })
})

// Issue #109 — AGENTS.md is a virtual tree entry, always present,
// never deletable, routed through ``updateAgent`` on Save.
describe('ManifestPanel — AGENTS.md virtual entry', () => {
  it('always shows AGENTS.md at the top of the tree, even when agents_md is null', async () => {
    renderDialog()
    const row = await screen.findByTestId('agent-edit-file-AGENTS.md')
    expect(row).toBeInTheDocument()
    expect(row).toHaveAttribute('data-virtual', 'true')
  })

  it('selects AGENTS.md by default when the dialog opens', async () => {
    renderDialog([
      {
        path: 'skills/greet/SKILL.md',
        content: 'hello',
        updated_at: '2026-04-18T00:00:00Z',
      },
    ])
    // The editor's textarea is bound to the selected row's content;
    // a null ``agents_md`` agent renders empty content when AGENTS.md
    // is selected. Skill file content should NOT appear.
    await screen.findByTestId('agent-edit-file-AGENTS.md')
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    expect(textarea.value).toBe('')
  })

  it('does not render a trash icon on the AGENTS.md row', async () => {
    renderDialog()
    const row = await screen.findByTestId('agent-edit-file-AGENTS.md')
    // The trash button's title ``Remove <path>`` is gated on
    // ``!f.virtual``, so the AGENTS.md row must not contain one.
    expect(row.querySelector('button[title^="Remove"]')).toBeNull()
  })

  it('routes AGENTS.md Save through updateAgent with agents_md_set', async () => {
    const { updateAgent, upsertAgentFile } = renderDialog()
    await screen.findByTestId('agent-edit-file-AGENTS.md')
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '# role\n\nYou are a helper.' } })
    fireEvent.click(screen.getByTestId('agent-edit-save'))

    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
    expect(updateAgent).toHaveBeenCalledWith('a1', {
      agents_md: '# role\n\nYou are a helper.',
      agents_md_set: true,
    })
    // Non-virtual files should NOT upsert when only AGENTS.md changed.
    expect(upsertAgentFile).not.toHaveBeenCalled()
  })

  it('clears agents_md to null when AGENTS.md is saved with empty content', async () => {
    const { updateAgent } = renderDialog()
    await screen.findByTestId('agent-edit-file-AGENTS.md')
    // Seed some content then clear it so dirty triggers.
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'temp' } })
    fireEvent.change(textarea, { target: { value: '' } })
    fireEvent.click(screen.getByTestId('agent-edit-save'))

    await waitFor(() => expect(updateAgent).toHaveBeenCalledTimes(1))
    expect(updateAgent).toHaveBeenCalledWith('a1', {
      agents_md: null,
      agents_md_set: true,
    })
  })

  it('rejects AGENTS.md as a "New file" path with a clear message', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-file-AGENTS.md')
    fireEvent.click(screen.getByTestId('agent-edit-toggle-new-file'))
    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: 'AGENTS.md' } })
    fireEvent.click(screen.getByText('Add'))

    expect(
      await screen.findByText(/AGENTS\.md already exists/i),
    ).toBeInTheDocument()
  })
})

// Issue #112 — skill-aware manifest tree pieces.
describe('buildTree (Issue #112)', () => {
  const skillsOnly = ['skills/'] as const
  const claudeAndSkills = ['skills/', '.claude/'] as const

  it('returns an empty forest for no files', () => {
    expect(buildTree([], skillsOnly)).toEqual([])
  })

  it('places the virtual AGENTS.md row at the root', () => {
    const files = [
      {
        path: 'AGENTS.md',
        content: '# role',
        updated_at: '2026-04-18',
        originalContent: '# role',
        dirty: false,
        deleted: false,
        virtual: true,
      },
    ] as const
    const tree = buildTree(files as never, skillsOnly)
    expect(tree).toHaveLength(1)
    expect(tree[0]).toMatchObject({ kind: 'file', path: 'AGENTS.md' })
  })

  it('builds nested dir nodes for deep skill paths', () => {
    const files = [
      {
        path: 'skills/greet/SKILL.md',
        content: '# greet',
        updated_at: '2026-04-18',
        originalContent: '# greet',
        dirty: false,
        deleted: false,
      },
      {
        path: 'skills/greet/scripts/helper.sh',
        content: '#!/bin/bash',
        updated_at: '2026-04-18',
        originalContent: '#!/bin/bash',
        dirty: false,
        deleted: false,
      },
    ] as const
    const tree = buildTree(files as never, skillsOnly)
    // Only one root: skills/
    expect(tree).toHaveLength(1)
    const skillsDir = tree[0] as Extract<TreeNode, { kind: 'dir' }>
    expect(skillsDir.kind).toBe('dir')
    expect(skillsDir.name).toBe('skills')
    // Below: greet/ dir → SKILL.md + scripts/
    const greetDir = skillsDir.children.find(c => c.kind === 'dir' && c.name === 'greet') as
      | Extract<TreeNode, { kind: 'dir' }>
      | undefined
    expect(greetDir).toBeDefined()
    expect(greetDir!.children.map(c => c.name).sort()).toEqual(['SKILL.md', 'scripts'])
    const scriptsDir = greetDir!.children.find(c => c.name === 'scripts') as
      | Extract<TreeNode, { kind: 'dir' }>
      | undefined
    expect(scriptsDir!.kind).toBe('dir')
    expect(scriptsDir!.children[0]).toMatchObject({ kind: 'file', name: 'helper.sh' })
  })

  it('skips files whose prefix is not admitted by the engine filter', () => {
    const files = [
      {
        path: '.codex/config.toml',
        content: '',
        updated_at: '2026-04-18',
        originalContent: '',
        dirty: false,
        deleted: false,
      },
      {
        path: '.claude/settings.json',
        content: '{}',
        updated_at: '2026-04-18',
        originalContent: '{}',
        dirty: false,
        deleted: false,
      },
    ] as const
    const tree = buildTree(files as never, claudeAndSkills)
    // .codex dropped; only .claude at root
    expect(tree).toHaveLength(1)
    expect(tree[0]).toMatchObject({ kind: 'dir', name: '.claude' })
  })

  it('drops deleted rows from the tree', () => {
    const files = [
      {
        path: 'skills/old/SKILL.md',
        content: '',
        updated_at: '2026-04-18',
        originalContent: '',
        dirty: false,
        deleted: true,
      },
    ] as const
    expect(buildTree(files as never, skillsOnly)).toEqual([])
  })
})

describe('isSkillDirNode (Issue #112)', () => {
  it('matches ``skills/<name>`` at depth 2', () => {
    expect(
      isSkillDirNode({ kind: 'dir', path: 'skills/greet', name: 'greet', children: [] }),
    ).toBe(true)
  })

  it('rejects ``skills/`` itself and deeper nested dirs', () => {
    expect(
      isSkillDirNode({ kind: 'dir', path: 'skills', name: 'skills', children: [] }),
    ).toBe(false)
    expect(
      isSkillDirNode({
        kind: 'dir',
        path: 'skills/greet/scripts',
        name: 'scripts',
        children: [],
      }),
    ).toBe(false)
  })

  it('rejects file nodes', () => {
    expect(
      isSkillDirNode({
        kind: 'file',
        path: 'skills/greet/SKILL.md',
        name: 'SKILL.md',
        file: {} as never,
      }),
    ).toBe(false)
  })
})

describe('slugifySkillName (Issue #112)', () => {
  it('lowercases and collapses whitespace to dashes', () => {
    expect(slugifySkillName('Code Review')).toBe('code-review')
    expect(slugifySkillName('  Hello   World  ')).toBe('hello-world')
  })

  it('strips non-alphanumerics', () => {
    expect(slugifySkillName('my_skill!@#')).toBe('my-skill')
  })

  it('returns empty string for purely non-alphanumeric input', () => {
    expect(slugifySkillName('!!!')).toBe('')
  })
})

describe('ManifestPanel — engine-based prefix filter (Issue #112)', () => {
  it('claude-code agent does not render .codex or .gemini groups', async () => {
    renderDialog([
      {
        path: '.codex/config.toml',
        content: '',
        updated_at: '2026-04-18T00:00:00Z',
      },
      {
        path: '.gemini/settings.json',
        content: '{}',
        updated_at: '2026-04-18T00:00:00Z',
      },
      {
        path: 'skills/test/SKILL.md',
        content: '',
        updated_at: '2026-04-18T00:00:00Z',
      },
    ])
    // skills/ is in claude-code's allowed set; wait for its dir node.
    await screen.findByTestId('agent-edit-dir-skills')
    expect(screen.queryByTestId('agent-edit-dir-.codex')).toBeNull()
    expect(screen.queryByTestId('agent-edit-dir-.gemini')).toBeNull()
  })
})

describe('ManifestPanel — skill quick-add button (Issue #112)', () => {
  it('prefills the New file form with the skill path', async () => {
    renderDialog([
      {
        path: 'skills/greet/SKILL.md',
        content: '',
        updated_at: '2026-04-18T00:00:00Z',
      },
    ])
    // Seed-expanded ``skills`` renders the greet dir node with the + button.
    const addButton = await screen.findByTestId('agent-edit-add-in-skill-greet')
    fireEvent.click(addButton)
    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    expect(pathInput.value).toBe('skills/greet/')
  })
})

describe('ManifestPanel — New skill action (Issue #112)', () => {
  it('creates skills/<slug>/SKILL.md with a frontmatter template', async () => {
    renderDialog()
    // Open the New skill form.
    fireEvent.click(await screen.findByTestId('agent-edit-toggle-new-skill'))
    const nameInput = (await screen.findByTestId(
      'agent-edit-new-skill-name',
    )) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'Code Review' } })
    fireEvent.click(screen.getByTestId('agent-edit-create-skill'))
    // The new SKILL.md row should now be in the tree and selected.
    await screen.findByTestId('agent-edit-file-skills/code-review/SKILL.md')
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    expect(textarea.value).toContain('name: code-review')
    expect(textarea.value).toContain('description: TODO')
    expect(textarea.value).toContain('# code-review')
  })

  it('rejects a skill name that slugifies to empty', async () => {
    renderDialog()
    fireEvent.click(await screen.findByTestId('agent-edit-toggle-new-skill'))
    const nameInput = (await screen.findByTestId(
      'agent-edit-new-skill-name',
    )) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: '!!!' } })
    fireEvent.click(screen.getByTestId('agent-edit-create-skill'))
    expect(
      await screen.findByText(/must contain at least one alphanumeric/i),
    ).toBeInTheDocument()
  })
})

describe('ManifestPanel — script extensions (Issue #112)', () => {
  it('admits ``.sh`` as a valid path in the New file form', async () => {
    renderDialog()
    await screen.findByTestId('agent-edit-upload')
    fireEvent.click(screen.getByTestId('agent-edit-toggle-new-file'))
    const pathInput = (await screen.findByTestId(
      'agent-edit-new-file-path',
    )) as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: 'skills/greet/scripts/helper.sh' } })
    fireEvent.click(screen.getByText('Add'))
    // No validation error message.
    expect(screen.queryByText(/extension must be one of/i)).toBeNull()
    expect(screen.queryByText(/path must start with/i)).toBeNull()
    // The form closes on successful add (``showNewFileForm=false``).
    await waitFor(() =>
      expect(screen.queryByTestId('agent-edit-new-file-path')).toBeNull(),
    )
  })
})

describe('ManifestPanel — attached library skills (Issue #133)', () => {
  it('does not render the section when no skills are attached', async () => {
    renderDialog([], { attachedSkills: [] })
    await screen.findByTestId('agent-edit-upload')
    // Toggle / section / items all keyed by testids.
    expect(
      screen.queryByTestId('agent-edit-attached-skills-toggle'),
    ).toBeNull()
  })

  it('renders the attached skills section and loads SKILL.md on select', async () => {
    const skills: AttachedSkill[] = [
      {
        id: 'sk-1',
        name: 'web-design-guidelines',
        source: 'vercel-labs/agent-skills',
        pinned_rev: 'ce3e64e4',
        extra_files: [],
      },
    ]
    const preview: SkillPreview = {
      id: 'sk-1',
      name: 'web-design-guidelines',
      skill_md: '# Web Design\n\nFollow the design system.',
      extra_files: ['references/guide.md'],
    }
    const { fetchSkillPreview } = renderDialog([], {
      attachedSkills: skills,
      skillPreview: preview,
    })
    await screen.findByTestId('agent-edit-upload')

    // Section header is visible.
    expect(
      screen.getByTestId('agent-edit-attached-skills-toggle'),
    ).toBeInTheDocument()
    const skillRow = screen.getByTestId(
      'agent-edit-attached-skill-web-design-guidelines',
    )
    fireEvent.click(skillRow)

    await waitFor(() =>
      expect(fetchSkillPreview).toHaveBeenCalledWith('sk-1'),
    )
    const textarea = (await screen.findByTestId(
      'agent-edit-attached-skill-content',
    )) as HTMLTextAreaElement
    expect(textarea.readOnly).toBe(true)
    expect(textarea.value).toContain('Follow the design system')
    // "View in Skills" link should be present for navigation.
    expect(screen.getByTestId('agent-edit-view-in-skills')).toBeInTheDocument()
  })
})

// Issue #479 — the panel re-seeded its editor working copy on every change
// to the ``agent`` prop's object identity. The parent (#281 pattern) derives
// a fresh Agent object from the live list on every ``useAgents.fetchAgents``
// (e.g. the #219 transitional poll, every 1.5s), so an in-progress AGENTS.md
// edit was clobbered repeatedly. The seed must run only when the agent's
// stable id changes (open / agent switch), never for a same-id refresh.
describe('ManifestPanel — edit preservation across agent prop refresh (#479)', () => {
  function renderPanel(agent: Agent, mocks: {
    fetchAgentFiles: ReturnType<typeof vi.fn>
    fetchAttachedSkills: ReturnType<typeof vi.fn>
  }) {
    const updateAgent = vi.fn().mockResolvedValue(makeAgent())
    const upsertAgentFile = vi.fn()
    const deleteAgentFile = vi.fn().mockResolvedValue(undefined)
    const fetchSkillPreview = vi.fn().mockResolvedValue(null)
    const props = {
      fetchAgentFiles: mocks.fetchAgentFiles,
      updateAgent,
      upsertAgentFile,
      deleteAgentFile,
      fetchAttachedSkills: mocks.fetchAttachedSkills,
      fetchSkillPreview,
    }
    const view = render(
      <MemoryRouter>
        <ManifestPanel agent={agent} {...props} />
      </MemoryRouter>,
    )
    const rerenderWith = (next: Agent) =>
      view.rerender(
        <MemoryRouter>
          <ManifestPanel agent={next} {...props} />
        </MemoryRouter>,
      )
    return { rerenderWith }
  }

  it('preserves an in-progress AGENTS.md edit when the agent prop is replaced with a new object of the same id', async () => {
    const fetchAgentFiles = vi.fn().mockResolvedValue([])
    const fetchAttachedSkills = vi.fn().mockResolvedValue([])
    const { rerenderWith } = renderPanel(
      makeAgent({ id: 'a1', agents_md: '# original' }),
      { fetchAgentFiles, fetchAttachedSkills },
    )

    await screen.findByTestId('agent-edit-file-AGENTS.md')
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    expect(textarea.value).toBe('# original')

    fireEvent.change(textarea, { target: { value: '# edited by user' } })
    expect(textarea.value).toBe('# edited by user')

    const loadsBefore = fetchAgentFiles.mock.calls.length

    // Simulate the #219 poll: setAgents replaces the list, so the parent
    // hands down a brand-new Agent object with the SAME id and unchanged
    // server content.
    rerenderWith(makeAgent({ id: 'a1', agents_md: '# original' }))
    // Let any (regressed) re-fired loadInitial resolve its fetch + setFiles.
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // The seed must NOT re-run for the same id (no extra file load) and the
    // edit must survive.
    expect(fetchAgentFiles).toHaveBeenCalledTimes(loadsBefore)
    expect(
      (screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement).value,
    ).toBe('# edited by user')
  })

  it('re-seeds AGENTS.md when the agent prop switches to a different id', async () => {
    const fetchAgentFiles = vi.fn().mockResolvedValue([])
    const fetchAttachedSkills = vi.fn().mockResolvedValue([])
    const { rerenderWith } = renderPanel(
      makeAgent({ id: 'a1', agents_md: '# original' }),
      { fetchAgentFiles, fetchAttachedSkills },
    )

    await screen.findByTestId('agent-edit-file-AGENTS.md')
    const textarea = screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '# edited by user' } })

    const loadsBefore = fetchAgentFiles.mock.calls.length

    // A genuinely different agent — the editor SHOULD reseed from its content.
    rerenderWith(makeAgent({ id: 'a2', agents_md: '# other agent' }))
    await waitFor(() =>
      expect(fetchAgentFiles.mock.calls.length).toBeGreaterThan(loadsBefore),
    )
    await waitFor(() =>
      expect(
        (screen.getByTestId('agent-edit-file-content') as HTMLTextAreaElement).value,
      ).toBe('# other agent'),
    )
  })
})
