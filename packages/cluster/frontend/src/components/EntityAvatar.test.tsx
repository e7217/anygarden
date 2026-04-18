// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Avoid pulling @lobehub/icons into the unit-test bundle. The overlay
// branch is what the avatar owns; we only need to assert it renders
// *something* addressable when agent + engine are supplied.
vi.mock('@/components/EngineGlyph', () => ({
  EngineGlyph: ({ engine, size }: { engine: string | undefined; size?: number }) => (
    <svg data-testid={`engine-${engine ?? 'none'}`} data-size={size ?? 16} />
  ),
}))

import { EntityAvatar } from './EntityAvatar'

afterEach(() => cleanup())

describe('EntityAvatar', () => {
  it('renders the initial fallback derived from name', () => {
    const { getByText } = render(
      <EntityAvatar id="u-1" name="Alice Kim" kind="user" />,
    )
    expect(getByText('AK')).toBeInTheDocument()
  })

  it('falls back to "?" when name is empty', () => {
    const { getByText } = render(<EntityAvatar id="u-1" name="" kind="user" />)
    expect(getByText('?')).toBeInTheDocument()
  })

  it('renders a single CJK initial for Korean names', () => {
    const { getByText } = render(
      <EntityAvatar id="u-1" name="김수현" kind="user" />,
    )
    expect(getByText('김')).toBeInTheDocument()
  })

  it('produces identical backgrounds for identical ids (determinism)', () => {
    const { container: c1 } = render(
      <EntityAvatar id="agent-42" name="A" kind="agent" data-testid="a" />,
    )
    const { container: c2 } = render(
      <EntityAvatar id="agent-42" name="B" kind="agent" data-testid="b" />,
    )
    const findFallback = (root: HTMLElement) =>
      root.querySelector('[data-testid="entity-avatar-fallback"]') as HTMLElement | null
    const bg1 = findFallback(c1)?.style.backgroundColor
    const bg2 = findFallback(c2)?.style.backgroundColor
    expect(bg1).toBeTruthy()
    expect(bg1).toBe(bg2)
  })

  it('renders the engine glyph overlay when kind=agent and engine is provided', () => {
    const { getByTestId } = render(
      <EntityAvatar
        id="agent-1"
        name="Planner"
        kind="agent"
        engine="claude-code"
      />,
    )
    expect(getByTestId('entity-avatar-engine-glyph')).toBeInTheDocument()
    // The mocked EngineGlyph receives the engine prop through.
    expect(getByTestId('engine-claude-code')).toBeInTheDocument()
  })

  it('omits the engine glyph when kind=agent but engine is missing', () => {
    const { queryByTestId } = render(
      <EntityAvatar id="agent-1" name="Planner" kind="agent" />,
    )
    expect(queryByTestId('entity-avatar-engine-glyph')).toBeNull()
  })

  it('omits the engine glyph when kind=user even with an engine', () => {
    const { queryByTestId } = render(
      <EntityAvatar id="u-1" name="Alice" kind="user" engine="claude-code" />,
    )
    expect(queryByTestId('entity-avatar-engine-glyph')).toBeNull()
  })

  it('omits the engine glyph when kind=room', () => {
    const { queryByTestId } = render(
      <EntityAvatar id="room-1" name="backend-chat" kind="room" />,
    )
    expect(queryByTestId('entity-avatar-engine-glyph')).toBeNull()
  })

  it('applies the dashed-border marker for kind=guest', () => {
    const { getByTestId } = render(
      <EntityAvatar
        id="g-1"
        name="Visitor"
        kind="guest"
        data-testid="guest-av"
      />,
    )
    const root = getByTestId('guest-av')
    expect(root.getAttribute('data-guest')).toBe('true')
  })

  it.each<['xs' | 'sm' | 'md' | 'lg', number]>([
    ['xs', 20],
    ['sm', 24],
    ['md', 32],
    ['lg', 40],
  ])('maps size=%s to %dpx width/height', (size, px) => {
    const { getByTestId } = render(
      <EntityAvatar
        id="x"
        name="X"
        kind="user"
        size={size}
        data-testid="sz"
      />,
    )
    const root = getByTestId('sz')
    expect(root.style.width).toBe(`${px}px`)
    expect(root.style.height).toBe(`${px}px`)
  })

  it('defaults to sm (24px) when size is omitted', () => {
    const { getByTestId } = render(
      <EntityAvatar id="x" name="X" kind="user" data-testid="sz" />,
    )
    const root = getByTestId('sz')
    expect(root.style.width).toBe('24px')
  })

  it('exposes data-testid on the outer wrapper', () => {
    const { getByTestId } = render(
      <EntityAvatar id="x" name="X" kind="user" data-testid="avatar-root" />,
    )
    expect(getByTestId('avatar-root')).toBeInTheDocument()
  })

  it('merges an incoming className with the wrapper', () => {
    const { getByTestId } = render(
      <EntityAvatar
        id="x"
        name="X"
        kind="user"
        className="mr-2"
        data-testid="sz"
      />,
    )
    const root = getByTestId('sz')
    expect(root.className).toContain('mr-2')
  })

  // Issue #101 — admin-customizable avatar (emoji / lucide).
  it('renders an emoji body when avatarKind=emoji', () => {
    const { getByTestId, queryByText } = render(
      <EntityAvatar
        id="a-1"
        name="Planner"
        kind="agent"
        avatarKind="emoji"
        avatarValue="🤖"
      />,
    )
    expect(getByTestId('entity-avatar-emoji')).toHaveTextContent('🤖')
    // The name-derived initial must NOT appear when the emoji takes over.
    expect(queryByText('P')).toBeNull()
  })

  it('renders a lucide icon body when avatarKind=lucide with a known name', () => {
    const { getByTestId, queryByText } = render(
      <EntityAvatar
        id="a-2"
        name="Searcher"
        kind="agent"
        avatarKind="lucide"
        avatarValue="Search"
      />,
    )
    expect(getByTestId('entity-avatar-lucide')).toBeInTheDocument()
    expect(queryByText('S')).toBeNull()
  })

  it('falls back to initials for an unknown lucide name', () => {
    const { queryByTestId, getByText } = render(
      <EntityAvatar
        id="a-3"
        name="Bogus Name"
        kind="agent"
        avatarKind="lucide"
        avatarValue="DefinitelyNotALucideIcon"
      />,
    )
    expect(queryByTestId('entity-avatar-lucide')).toBeNull()
    expect(queryByTestId('entity-avatar-emoji')).toBeNull()
    expect(getByText('BN')).toBeInTheDocument()
  })

  it('keeps the engine glyph overlay for agents even when a custom avatar is set', () => {
    const { getByTestId } = render(
      <EntityAvatar
        id="a-4"
        name="Claude"
        kind="agent"
        engine="claude-code"
        avatarKind="emoji"
        avatarValue="⚡"
      />,
    )
    expect(getByTestId('entity-avatar-emoji')).toHaveTextContent('⚡')
    expect(getByTestId('entity-avatar-engine-glyph')).toBeInTheDocument()
  })
})
