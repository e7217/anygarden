// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Mock the heavy @lobehub/icons sub-paths so the EngineGlyph branch
// matcher can be asserted without pulling real SVG assets (and their
// antd-style dependency chain) into the test runtime. Each mock
// exposes a `default` with a `.Color` attachment — the exact shape
// EngineGlyph consumes.
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
vi.mock('@lobehub/icons/es/OpenHands', () => {
  const Color = () => <svg data-testid="openhands-color" />
  const Icon = Object.assign(() => <svg data-testid="openhands-mono" />, {
    Color,
  })
  return { default: Icon }
})

import { EngineGlyph } from './EngineGlyph'

afterEach(() => cleanup())

describe('EngineGlyph', () => {
  it('renders Claude colored logo for claude engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="claude" />)
    expect(getByTestId('claude-color')).toBeInTheDocument()
  })

  it('renders Claude colored logo for claude-code variant', () => {
    const { getByTestId } = render(<EngineGlyph engine="claude-code" />)
    expect(getByTestId('claude-color')).toBeInTheDocument()
  })

  it('renders Claude colored logo for anthropic engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="anthropic" />)
    expect(getByTestId('claude-color')).toBeInTheDocument()
  })

  it('renders Codex logo (mono) for codex engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="codex" />)
    expect(getByTestId('codex-mono')).toBeInTheDocument()
  })

  it('renders Codex logo (mono) for openai engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="openai" />)
    expect(getByTestId('codex-mono')).toBeInTheDocument()
  })

  it('renders Gemini colored logo for gemini-cli variant', () => {
    const { getByTestId } = render(<EngineGlyph engine="gemini-cli" />)
    expect(getByTestId('gemini-color')).toBeInTheDocument()
  })

  it('renders OpenHands colored logo for openhands engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="openhands" />)
    expect(getByTestId('openhands-color')).toBeInTheDocument()
  })

  it('falls back to a lucide Bot icon for deep-agents (no brand mark)', () => {
    const { container } = render(<EngineGlyph engine="deep-agents" />)
    expect(container.querySelector('svg.lucide-bot')).toBeTruthy()
  })

  it('falls back to a lucide Bot icon for unknown engines', () => {
    const { container } = render(<EngineGlyph engine="some-unknown" />)
    expect(container.querySelector('svg.lucide-bot')).toBeTruthy()
  })

  it('falls back to a lucide Bot icon for undefined engine', () => {
    const { container } = render(<EngineGlyph engine={undefined} />)
    expect(container.querySelector('svg.lucide-bot')).toBeTruthy()
  })

  it('tolerates uppercase / mixed-case engine values', () => {
    const { getByTestId } = render(<EngineGlyph engine="CLAUDE" />)
    expect(getByTestId('claude-color')).toBeInTheDocument()
  })

  it('honors the size prop (passed through to the underlying icon)', () => {
    // lucide Bot applies the size as width/height. Check via the
    // fallback branch where the real lucide component renders.
    const { container } = render(<EngineGlyph engine="deep-agents" size={12} />)
    const svg = container.querySelector('svg.lucide-bot')
    expect(svg?.getAttribute('width')).toBe('12')
    expect(svg?.getAttribute('height')).toBe('12')
  })
})
