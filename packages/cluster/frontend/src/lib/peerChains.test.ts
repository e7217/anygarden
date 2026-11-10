import { describe, expect, it } from 'vitest'
import {
  MAX_THREAD_INDENT,
  activeChainsForMessage,
  branchMessageIds,
  buildChainTree,
  chainsForThread,
  currentActivity,
  defaultExpandedIds,
  messagePlacements,
  placementLabel,
  primaryChain,
} from './peerChains'
import { node, sampleChain } from './peerChains.fixtures'

describe('buildChainTree', () => {
  it('nests each question under the one whose answerer asked it', () => {
    const tree = buildChainTree(sampleChain())
    expect(tree.roots.map(entry => entry.node.id)).toEqual(['B', 'D'])
    expect(tree.byId.get('B')!.children.map(entry => entry.node.id)).toEqual(['C'])
    expect(tree.byId.get('F')!.children.map(entry => entry.node.id)).toEqual(['G'])
    expect(tree.byId.get('G')!.path).toEqual(['A', 'D', 'E', 'F', 'G'])
  })

  it('orders siblings by creation even when the payload is shuffled', () => {
    const chain = sampleChain()
    const tree = buildChainTree({ nodes: [...chain.nodes].reverse() })
    expect(tree.roots.map(entry => entry.node.id)).toEqual(['B', 'D'])
  })

  it('marks a branch open while anything below it still runs', () => {
    const tree = buildChainTree(sampleChain())
    expect(tree.byId.get('B')!.open).toBe(false)
    expect(tree.byId.get('D')!.open).toBe(true)
    expect(tree.byId.get('G')!.open).toBe(true)
  })

  it('keeps a question whose parent is missing at the top level', () => {
    const tree = buildChainTree({ nodes: [node({ id: 'X', parent_id: 'gone', depth: 3, caller_name: 'Q' })] })
    expect(tree.roots.map(entry => entry.node.id)).toEqual(['X'])
    expect(tree.byId.get('X')!.path).toEqual(['Q', 'X'])
  })

  it('survives a cyclic payload without looping', () => {
    const tree = buildChainTree({
      nodes: [node({ id: 'P', parent_id: 'Q' }), node({ id: 'Q', parent_id: 'P' })],
    })
    expect(tree.byId.size).toBe(2)
  })
})

describe('defaultExpandedIds', () => {
  it('opens the active path and folds finished branches', () => {
    const expanded = defaultExpandedIds(buildChainTree(sampleChain()))
    expect([...expanded].sort()).toEqual(['D', 'E', 'F'])
  })

  it('folds everything once the chain is done', () => {
    const chain = sampleChain()
    chain.nodes = chain.nodes.map(entry => ({ ...entry, state: 'completed' }))
    expect(defaultExpandedIds(buildChainTree(chain)).size).toBe(0)
  })
})

describe('branchMessageIds', () => {
  it('collects the question and reply of a node and every question under it', () => {
    const tree = buildChainTree(sampleChain())
    expect([...branchMessageIds(tree, 'B')].sort()).toEqual(['q-B', 'q-C', 'r-B', 'r-C'])
    expect([...branchMessageIds(tree, 'E')].sort()).toEqual(['q-E', 'q-F', 'q-G'])
  })

  it('is empty for an unknown node', () => {
    expect(branchMessageIds(buildChainTree(sampleChain()), 'nope').size).toBe(0)
  })
})

describe('placementLabel', () => {
  it('reads as a direction for shallow messages', () => {
    expect(placementLabel(['A', 'B'], 'question', 1)).toBe('A → B')
    expect(placementLabel(['A', 'B'], 'reply', 1)).toBe('B → A')
    expect(placementLabel(['A', 'D', 'E', 'F'], 'question', MAX_THREAD_INDENT)).toBe('E → F')
  })

  it('carries an abbreviated path once indentation stops growing', () => {
    const path = ['A', 'D', 'E', 'F', 'G']
    expect(placementLabel(path, 'question', 4)).toBe('… › E › F → G')
    expect(placementLabel(path, 'reply', 4)).toBe('… › E › F ← G')
  })
})

describe('messagePlacements', () => {
  it('indents by depth up to the cap and labels questions and replies', () => {
    const placements = messagePlacements([sampleChain()])
    expect(placements.get('q-B')).toMatchObject({ kind: 'question', indent: 1, label: 'A → B' })
    expect(placements.get('r-B')).toMatchObject({ kind: 'reply', indent: 1, label: 'B → A' })
    expect(placements.get('q-C')).toMatchObject({ indent: 2, label: 'B → C' })
    expect(placements.get('q-F')).toMatchObject({ indent: 3, label: 'E → F' })
    expect(placements.get('q-G')).toMatchObject({ depth: 4, indent: 3, label: '… › E › F → G' })
    expect(placements.has('r-G')).toBe(false)
  })
})

describe('chain selection', () => {
  const done = sampleChain({ id: 'old', state: 'done', started_at: '2026-11-09T00:00:00Z' })
  const running = sampleChain()
  const elsewhere = sampleChain({ id: 'other', thread_root_id: 'human-2', origin_message_id: 'human-2' })

  it('finds the chains of a thread and prefers a running one', () => {
    const chains = chainsForThread([done, elsewhere, running], 'human-1')
    expect(chains.map(chain => chain.id)).toEqual(['chain-1', 'old'])
    expect(primaryChain([done, running])!.id).toBe('chain-1')
    expect(primaryChain([done])!.id).toBe('old')
    expect(primaryChain([])).toBeNull()
  })

  it('anchors a progress card on the thread root, only while active', () => {
    const fromReply = sampleChain({ id: 'nested', origin_message_id: 'reply-9' })
    expect(activeChainsForMessage([done, running, fromReply], 'human-1').map(chain => chain.id).sort())
      .toEqual(['chain-1', 'nested'])
    expect(activeChainsForMessage([done], 'human-1')).toEqual([])
  })
})

describe('currentActivity', () => {
  it('names the latest answerer and counts the others', () => {
    expect(currentActivity(sampleChain())).toEqual({ kind: 'answering', name: 'G', others: 0 })
    const chain = sampleChain()
    chain.nodes = [...chain.nodes, node({ id: 'H', state: 'answering' })]
    expect(currentActivity(chain)).toEqual({ kind: 'answering', name: 'H', others: 1 })
  })

  it('falls back to queued questions, then to the root finishing up', () => {
    const queued = sampleChain()
    queued.nodes = queued.nodes.map(entry => ({ ...entry, state: entry.id === 'G' ? 'pending' : 'completed' }))
    expect(currentActivity(queued)).toEqual({ kind: 'pending', name: 'G', others: 0 })
    const finishing = sampleChain()
    finishing.nodes = finishing.nodes.map(entry => ({ ...entry, state: 'completed' }))
    expect(currentActivity(finishing)).toEqual({ kind: 'finishing', name: 'A' })
  })
})
