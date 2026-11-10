/**
 * #1006 — multi-level agent question chains.
 *
 * An agent asks a peer through ``ask_peer``; the peer may ask further
 * peers, without a depth limit. Every question and answer of one human
 * message lands in that message's thread. The server reports each chain
 * as a flat list of nodes (one per question); this module turns that
 * list into the tree, labels and filters the UI shows. Pure functions
 * only — fetching lives in ``hooks/usePeerChains``.
 */

export type PeerChainState = 'active' | 'done' | 'cancelled'

export type PeerNodeState =
  | 'pending'
  | 'answering'
  | 'waiting'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'

export interface PeerChainAgent {
  participant_id: string
  agent_id: string | null
  name: string
}

export interface PeerChainNode {
  id: string
  /** Node of the question whose answerer asked this one; null = asked by the root. */
  parent_id: string | null
  /** 1 = asked by the root agent. */
  depth: number
  caller_participant_id: string
  caller_name: string
  target_participant_id: string
  target_agent_id: string | null
  target_name: string
  question: string
  state: PeerNodeState
  reason: string | null
  question_message_id: string | null
  reply_message_id: string | null
  created_at: string
  finished_at: string | null
}

export interface PeerChain {
  id: string
  room_id: string
  /** The human message the thread hangs under. */
  thread_root_id: string | null
  origin_message_id: string
  state: PeerChainState
  started_at: string
  finished_at: string | null
  max_depth: number
  /** Distinct agents involved, the root included. */
  agent_count: number
  /** Number of questions asked. */
  call_count: number
  root: PeerChainAgent
  nodes: PeerChainNode[]
}

/** States in which a question still holds a turn open. */
const OPEN_STATES: ReadonlySet<PeerNodeState> = new Set(['pending', 'answering', 'waiting'])

export function isOpenNodeState(state: PeerNodeState): boolean {
  return OPEN_STATES.has(state)
}

export interface ChainTreeNode {
  node: PeerChainNode
  children: ChainTreeNode[]
  /** Names from the root agent down to this node's target. */
  path: string[]
  /** This node or one below it is still open. */
  open: boolean
}

export interface ChainTree {
  roots: ChainTreeNode[]
  byId: Map<string, ChainTreeNode>
}

function byCreation(a: PeerChainNode, b: PeerChainNode): number {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
}

/**
 * Nests a chain's flat node list under the questions that caused them.
 *
 * A node whose parent is not in the list (a partial payload) is kept at
 * the top level rather than dropped, so every question stays reachable.
 */
export function buildChainTree(chain: Pick<PeerChain, 'nodes'>): ChainTree {
  const sorted = [...chain.nodes].sort(byCreation)
  const byId = new Map<string, ChainTreeNode>()
  for (const node of sorted) {
    byId.set(node.id, { node, children: [], path: [], open: false })
  }
  const roots: ChainTreeNode[] = []
  for (const node of sorted) {
    const entry = byId.get(node.id)!
    const parent = node.parent_id && node.parent_id !== node.id ? byId.get(node.parent_id) : undefined
    if (parent) parent.children.push(entry)
    else roots.push(entry)
  }

  // Paths top-down, open flags bottom-up. Iterative so a pathological
  // depth cannot overflow the stack; ``seen`` guards against a cycle in
  // a malformed payload.
  const order: ChainTreeNode[] = []
  const seen = new Set<string>()
  const stack = roots.map(entry => ({ entry, parentPath: null as string[] | null }))
  stack.reverse()
  while (stack.length > 0) {
    const { entry, parentPath } = stack.pop()!
    if (seen.has(entry.node.id)) continue
    seen.add(entry.node.id)
    entry.path = parentPath
      ? [...parentPath, entry.node.target_name]
      : [entry.node.caller_name, entry.node.target_name]
    order.push(entry)
    for (let i = entry.children.length - 1; i >= 0; i--) {
      stack.push({ entry: entry.children[i], parentPath: entry.path })
    }
  }
  for (let i = order.length - 1; i >= 0; i--) {
    const entry = order[i]
    entry.open = isOpenNodeState(entry.node.state) || entry.children.some(child => child.open)
  }
  return { roots, byId }
}

/**
 * Rows expanded on first render: a branch with work still running below
 * it opens so the active path is visible; finished branches stay folded.
 */
export function defaultExpandedIds(tree: ChainTree): Set<string> {
  const expanded = new Set<string>()
  for (const entry of tree.byId.values()) {
    if (entry.children.some(child => child.open)) expanded.add(entry.node.id)
  }
  return expanded
}

/** The question and reply messages of a node and everything asked below it. */
export function branchMessageIds(tree: ChainTree, nodeId: string): Set<string> {
  const ids = new Set<string>()
  const start = tree.byId.get(nodeId)
  if (!start) return ids
  const stack = [start]
  const seen = new Set<string>()
  while (stack.length > 0) {
    const entry = stack.pop()!
    if (seen.has(entry.node.id)) continue
    seen.add(entry.node.id)
    if (entry.node.question_message_id) ids.add(entry.node.question_message_id)
    if (entry.node.reply_message_id) ids.add(entry.node.reply_message_id)
    stack.push(...entry.children)
  }
  return ids
}

/** Indent levels a thread message may take before its path is abbreviated. */
export const MAX_THREAD_INDENT = 3

export type PlacementKind = 'question' | 'reply'

export interface MessagePlacement {
  nodeId: string
  kind: PlacementKind
  depth: number
  /** Indent levels, capped at ``MAX_THREAD_INDENT``. */
  indent: number
  /** Visible label: "B → C" for a question, "C → B" for its reply;
   *  past the indent cap, an abbreviated path "… › B › C → D". */
  label: string
  /** Every name from the root agent to the answerer, for a tooltip. */
  fullPath: string[]
}

/**
 * The label for one question or reply. Shallow messages read as a
 * direction ("B → C"); past ``MAX_THREAD_INDENT`` the indent stops
 * growing, so the label carries the ancestry instead, trimmed to the
 * last three names so it still fits a phone.
 */
export function placementLabel(path: string[], kind: PlacementKind, depth: number): string {
  const target = path[path.length - 1] ?? ''
  const caller = path[path.length - 2] ?? ''
  const arrow = kind === 'question' ? `${caller} → ${target}` : `${target} → ${caller}`
  if (depth <= MAX_THREAD_INDENT || path.length <= 2) return arrow
  const above = path[path.length - 3]
  return kind === 'question'
    ? `… › ${above} › ${caller} → ${target}`
    : `… › ${above} › ${caller} ← ${target}`
}

/** Maps every question/reply message of the given chains to its place. */
export function messagePlacements(chains: PeerChain[]): Map<string, MessagePlacement> {
  const placements = new Map<string, MessagePlacement>()
  for (const chain of chains) {
    const tree = buildChainTree(chain)
    for (const entry of tree.byId.values()) {
      const { node, path } = entry
      const depth = Math.max(1, node.depth || path.length - 1)
      const indent = Math.min(depth, MAX_THREAD_INDENT)
      const place = (messageId: string | null, kind: PlacementKind) => {
        if (!messageId) return
        placements.set(messageId, {
          nodeId: node.id,
          kind,
          depth,
          indent,
          label: placementLabel(path, kind, depth),
          fullPath: path,
        })
      }
      place(node.question_message_id, 'question')
      place(node.reply_message_id, 'reply')
    }
  }
  return placements
}

/** The top-level message a chain belongs to: its thread root, else its origin. */
export function chainAnchorId(chain: Pick<PeerChain, 'thread_root_id' | 'origin_message_id'>): string {
  return chain.thread_root_id ?? chain.origin_message_id
}

/** Chains whose questions live in the thread under ``rootMessageId``, newest first. */
export function chainsForThread(chains: PeerChain[], rootMessageId: string): PeerChain[] {
  return newestFirst(chains.filter(chain => chainAnchorId(chain) === rootMessageId))
}

/**
 * Active chains whose progress card sits under ``messageId``. Anchored
 * on the thread root rather than the origin so a chain started by a
 * message inside a thread still shows under a message the main timeline
 * renders.
 */
export function activeChainsForMessage(chains: PeerChain[], messageId: string): PeerChain[] {
  return newestFirst(
    chains.filter(chain => chain.state === 'active' && chainAnchorId(chain) === messageId),
  )
}

function newestFirst(chains: PeerChain[]): PeerChain[] {
  return [...chains].sort((a, b) => (a.started_at < b.started_at ? 1 : a.started_at > b.started_at ? -1 : 0))
}

/** The chain a thread panel shows: a running one wins, then the newest. */
export function primaryChain(chains: PeerChain[]): PeerChain | null {
  const sorted = newestFirst(chains)
  return sorted.find(chain => chain.state === 'active') ?? sorted[0] ?? null
}

export type ChainActivity =
  | { kind: 'answering'; name: string; others: number }
  | { kind: 'pending'; name: string; others: number }
  | { kind: 'finishing'; name: string }

/**
 * What a running chain is doing right now, for the one-line card.
 * The most recently started answerer leads; with nobody answering or
 * queued, the root agent is writing its final answer.
 */
export function currentActivity(chain: PeerChain): ChainActivity {
  const latestFirst = [...chain.nodes].sort((a, b) => -byCreation(a, b))
  for (const kind of ['answering', 'pending'] as const) {
    const names = unique(latestFirst.filter(node => node.state === kind).map(node => node.target_name))
    if (names.length > 0) return { kind, name: names[0], others: names.length - 1 }
  }
  return { kind: 'finishing', name: chain.root.name }
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values))
}
