// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Mock the heavy @lobehub/icons sub-paths so the EngineGlyph branch
// matcher can be asserted without pulling real SVG assets (and their
// antd-style dependency chain) into the test runtime. Each mock
// exposes a `default` with a `.Color` attachment — the exact shape
// AgentNode consumes.
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

import { EngineGlyph } from './AgentNode'

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

  it('renders Codex logo (mono) for codex engine', () => {
    const { getByTestId } = render(<EngineGlyph engine="codex" />)
    expect(getByTestId('codex-mono')).toBeInTheDocument()
  })

  it('renders Gemini colored logo for gemini-cli variant', () => {
    const { getByTestId } = render(<EngineGlyph engine="gemini-cli" />)
    expect(getByTestId('gemini-color')).toBeInTheDocument()
  })

  it('falls back to a lucide Bot icon for unknown engines', () => {
    const { container } = render(<EngineGlyph engine="some-unknown" />)
    // lucide Bot renders an <svg> with class "lucide lucide-bot"
    expect(container.querySelector('svg.lucide-bot')).toBeTruthy()
  })

  it('tolerates uppercase / mixed-case engine values', () => {
    const { getByTestId } = render(<EngineGlyph engine="CLAUDE" />)
    expect(getByTestId('claude-color')).toBeInTheDocument()
  })
})
