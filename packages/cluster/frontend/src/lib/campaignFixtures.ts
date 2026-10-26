// Test fixtures for campaigns (#930). Imported only by tests.
import type { Campaign, CampaignAttempt } from './campaigns'

export function campaignFixture(overrides: Partial<Campaign> = {}): Campaign {
  return {
    id: 'camp-1',
    room_id: 'room-1',
    owner_id: 'user-1',
    agent_id: 'agent-1',
    title: 'Raise parser accuracy',
    purpose: 'Release blocker',
    mode: 'target',
    metric: { name: 'accuracy', direction: 'maximize', target: 0.9, min_delta: 0 },
    evaluator: { command: 'python eval.py', cwd: '.', timeout_sec: 600, pin_paths: [], score_key: 'score' },
    trusted_eval: 'none',
    budget: { max_total_tokens: 100000, retro_reserve_ratio: 0.05 },
    usable_tokens: 95000,
    deadline_at: null,
    status: 'active',
    stop_reason: null,
    status_detail: null,
    cycles_run: 0,
    tokens_used: 0,
    next_cycle_at: null,
    cycles_since_improvement: 0,
    consecutive_eval_failures: 0,
    retro_due: null,
    state_revision: 1,
    created_at: '2026-10-26T00:00:00Z',
    updated_at: '2026-10-26T00:00:00Z',
    best: null,
    final: null,
    open_attempt: null,
    ...overrides,
  }
}

export function attemptFixture(overrides: Partial<CampaignAttempt> = {}): CampaignAttempt {
  return {
    id: 'att-1',
    campaign_id: 'camp-1',
    cycle_no: 1,
    kind: 'cycle',
    task_id: null,
    opened_at: '2026-10-26T00:00:00Z',
    closed_at: null,
    agent_outcome: null,
    hypothesis: null,
    change_summary: null,
    artifact_ref: null,
    lessons: null,
    eval_status: 'pending',
    dev_score: null,
    valid: null,
    trusted_score: null,
    tokens: null,
    outcome: null,
    ...overrides,
  }
}
