import type { PeerChain, PeerChainNode } from '@/lib/peerChains'

let clock = 0

export function node(overrides: Partial<PeerChainNode> & Pick<PeerChainNode, 'id'>): PeerChainNode {
  clock += 1
  return {
    parent_id: null,
    depth: 1,
    caller_participant_id: 'p-a',
    caller_name: 'A',
    target_participant_id: `p-${overrides.id}`,
    target_agent_id: null,
    target_name: overrides.id,
    question: `question ${overrides.id}`,
    state: 'completed',
    reason: null,
    question_message_id: `q-${overrides.id}`,
    reply_message_id: `r-${overrides.id}`,
    created_at: `2026-11-10T00:00:${String(clock).padStart(2, '0')}Z`,
    finished_at: null,
    ...overrides,
  }
}

/**
 * A asks B (done; B asked C, done) and D (still running: D → E → F → G,
 * G answering). Seven agents, six questions, depth 4.
 */
export function sampleChain(overrides: Partial<PeerChain> = {}): PeerChain {
  clock = 0
  const nodes = [
    node({ id: 'B' }),
    node({ id: 'C', parent_id: 'B', depth: 2, caller_name: 'B' }),
    node({ id: 'D', state: 'waiting', reply_message_id: null }),
    node({ id: 'E', parent_id: 'D', depth: 2, caller_name: 'D', state: 'waiting', reply_message_id: null }),
    node({ id: 'F', parent_id: 'E', depth: 3, caller_name: 'E', state: 'waiting', reply_message_id: null }),
    node({ id: 'G', parent_id: 'F', depth: 4, caller_name: 'F', state: 'answering', reply_message_id: null }),
  ]
  return {
    id: 'chain-1',
    room_id: 'room-1',
    thread_root_id: 'human-1',
    origin_message_id: 'human-1',
    state: 'active',
    started_at: '2026-11-10T00:00:00Z',
    finished_at: null,
    max_depth: 4,
    agent_count: 7,
    call_count: 6,
    root: { participant_id: 'p-a', agent_id: 'agent-a', name: 'A' },
    nodes,
    ...overrides,
  }
}
