// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// React Flow pulls heavy WASM/DOM deps on import in some versions;
// stub Handle + Position so RoomNode can mount without bootstrapping a
// ReactFlowProvider. Same pattern as AgentNode.test.tsx.
vi.mock('@xyflow/react', () => {
  return {
    Handle: ({ type }: { type: string }) => (
      <span data-testid={`handle-${type}`} />
    ),
    Position: { Top: 'top', Bottom: 'bottom' },
  }
})

import { RoomNode } from './RoomNode'

afterEach(() => cleanup())

describe('RoomNode typing pulse (#84)', () => {
  // NodeProps is structural; we only feed the fields RoomNode reads.
  const renderNode = (data: Record<string, unknown>, selected = false) =>
    render(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      <RoomNode data={data} selected={selected} {...({} as any)} />,
    )

  it('applies .room-node--active when data.is_typing is true', () => {
    const { container } = renderNode({
      label: 'general',
      is_dm: false,
      participant_count: 3,
      is_typing: true,
    })
    const pill = container.querySelector('.room-node')
    expect(pill).toBeTruthy()
    expect(pill?.classList.contains('room-node--active')).toBe(true)
  })

  it('omits .room-node--active when data.is_typing is false', () => {
    const { container } = renderNode({
      label: 'general',
      is_dm: false,
      participant_count: 3,
      is_typing: false,
    })
    const pill = container.querySelector('.room-node')
    expect(pill?.classList.contains('room-node--active')).toBe(false)
  })

  it('omits .room-node--active when is_typing is missing', () => {
    // Forward-compat: payloads without the flag (older cache, server
    // without the typing tracker wired up) must still render plainly.
    const { container } = renderNode({
      label: 'general',
      is_dm: false,
      participant_count: 1,
    })
    const pill = container.querySelector('.room-node')
    expect(pill).toBeTruthy()
    expect(pill?.classList.contains('room-node--active')).toBe(false)
  })

  it('keeps the active class even when the node is selected', () => {
    // Selection paints a Notion Blue border (inline style); the typing
    // pulse paints a box-shadow ring (CSS class). They live on the
    // same element and must compose without one cancelling the other.
    const { container } = renderNode(
      {
        label: 'general',
        is_dm: false,
        participant_count: 2,
        is_typing: true,
      },
      true,
    )
    const pill = container.querySelector('.room-node')
    expect(pill).toBeTruthy()
    expect(pill?.classList.contains('room-node--active')).toBe(true)
  })
})
