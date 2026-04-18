// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Mock the heavy @lobehub/icons sub-paths so the EngineGlyph branch
// matcher can be asserted without pulling real SVG assets (and their
// antd-style dependency chain) into the test runtime. Each mock
// exposes a `default` with a `.Color` attachment — the exact shape
// AgentNode consumes (OpenAI / Anthropic bundles do not ship a
// `.Color`, but we currently do not render their `.Color` variants
// anyway so the mock shape is uniform for test simplicity).
vi.mock('@lobehub/icons/es/Claude', () => {
  const Color = () => <svg data-testid="claude-color" />
  const Icon = Object.assign(() => <svg data-testid="claude-mono" />, {
    Color,
  })
  return { default: Icon }
})
vi.mock('@lobehub/icons/es/Codex', () => {
  const Color = () => <svg data-testid="codex-color" />
  const Icon = Object.assign(() => <svg data-testid="codex-mono" />, {
    Color,
  })
  return { default: Icon }
})
vi.mock('@lobehub/icons/es/Gemini', () => {
  const Color = () => <svg data-testid="gemini-color" />
  const Icon = Object.assign(() => <svg data-testid="gemini-mono" />, {
    Color,
  })
  return { default: Icon }
})
vi.mock('@lobehub/icons/es/OpenAI', () => {
  const Icon = Object.assign(() => <svg data-testid="openai-mono" />, {})
  return { default: Icon }
})
vi.mock('@lobehub/icons/es/Anthropic', () => {
  const Icon = Object.assign(() => <svg data-testid="anthropic-mono" />, {})
  return { default: Icon }
})
vi.mock('@lobehub/icons/es/OpenHands', () => {
  const Color = () => <svg data-testid="openhands-color" />
  const Icon = Object.assign(() => <svg data-testid="openhands-mono" />, {
    Color,
  })
  return { default: Icon }
})

// React Flow pulls heavy WASM/DOM deps on import in some versions;
// stub Handle + Position so we can unit-test AgentNode without
// bootstrapping a ReactFlowProvider. NodeProps is a plain structural
// type (data + selected) so no further stubs are needed.
vi.mock('@xyflow/react', () => {
  return {
    Handle: ({ type }: { type: string }) => (
      <span data-testid={`handle-${type}`} />
    ),
    Position: { Top: 'top', Bottom: 'bottom' },
  }
})

import { AgentNode } from './AgentNode'

afterEach(() => cleanup())

describe('AgentNode', () => {
  // React Flow's NodeProps is a structural type — we only exercise
  // the fields AgentNode reads, so an `as any` cast keeps the test
  // free of the full NodeProps surface.
  const renderNode = (
    data: Record<string, unknown>,
    selected = false,
  ) =>
    render(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <AgentNode data={data} selected={selected} {...({} as any)} />,
    )

  it('applies .agent-node--running when actual_state is running', () => {
    const { container } = renderNode({
      engine: 'claude-code',
      actual_state: 'running',
      label: 'planner',
    })
    const pill = container.querySelector('.agent-node')
    expect(pill).toBeTruthy()
    expect(pill?.classList.contains('agent-node--running')).toBe(true)
  })

  it('does not apply .agent-node--running when actual_state is idle', () => {
    const { container } = renderNode({
      engine: 'claude-code',
      actual_state: 'idle',
      label: 'planner',
    })
    const pill = container.querySelector('.agent-node')
    expect(pill?.classList.contains('agent-node--running')).toBe(false)
  })

  it('includes label, engine, and state in aria-label', () => {
    const { container } = renderNode({
      engine: 'codex',
      actual_state: 'running',
      label: 'code-reviewer',
    })
    const pill = container.querySelector('.agent-node')
    const aria = pill?.getAttribute('aria-label') ?? ''
    expect(aria).toContain('code-reviewer')
    expect(aria).toContain('codex')
    expect(aria).toContain('running')
  })

  it('renders a very long label without throwing (clipping is CSS)', () => {
    const longLabel = 'x'.repeat(40)
    const { container } = renderNode({
      engine: 'gemini-cli',
      actual_state: 'idle',
      label: longLabel,
    })
    const labelEl = container.querySelector('.agent-node__label')
    expect(labelEl?.textContent).toBe(longLabel)
  })

  it('marks the glyph and state dot as aria-hidden (decorative)', () => {
    const { container } = renderNode({
      engine: 'claude-code',
      actual_state: 'running',
      label: 'planner',
    })
    const glyph = container.querySelector('.agent-node__glyph')
    const dot = container.querySelector('.agent-node__dot')
    expect(glyph?.getAttribute('aria-hidden')).toBe('true')
    expect(dot?.getAttribute('aria-hidden')).toBe('true')
  })

  it('falls back to "unknown" in aria-label when engine is empty', () => {
    const { container } = renderNode({
      engine: '',
      actual_state: 'idle',
      label: 'solo',
    })
    const pill = container.querySelector('.agent-node')
    expect(pill?.getAttribute('aria-label')).toContain('unknown')
  })
})
