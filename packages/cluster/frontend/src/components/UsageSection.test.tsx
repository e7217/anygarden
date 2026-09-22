// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { UsageSection } from './UsageSection'
import { apiFetch } from '@/lib/api'

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const report = {
  window_hours: 24, total_requests: 2, total_cost_usd: 0.025,
  by_model: [{ key: 'saved-model', request_count: 2, prompt_tokens: 50, completion_tokens: 10, cost_usd: 0.025 }],
  by_agent: [{ key: 'historical-agent', request_count: 2, prompt_tokens: 50, completion_tokens: 10, cost_usd: 0.025 }],
}

describe('Usage screen', () => {
  it('uses the neutral API and preserves period, model, agent, token and cost summaries', async () => {
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify(report)))
    render(<UsageSection />)
    await waitFor(() => expect(screen.getByText('saved-model')).toBeTruthy())
    expect(screen.getByText('historical-agent')).toBeTruthy()
    expect(screen.getByText('$0.0250')).toBeTruthy()
    expect(screen.getByText('60')).toBeTruthy()
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/usage?window=24h')
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify({ ...report, window_hours: 168 })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '7d' } })
    await waitFor(() => expect(apiFetch).toHaveBeenLastCalledWith('/api/v1/usage?window=7d'))
    expect(screen.queryByText('Apply')).toBeNull()
  })
  it('shows API failures and retries using Refresh', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'denied' }), { status: 403 }))
    render(<UsageSection />)
    await waitFor(() => expect(screen.getByText("Couldn't load usage: Failed to load usage (403)")).toBeTruthy())
    vi.mocked(apiFetch).mockResolvedValue(new Response(JSON.stringify(report)))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(screen.getByText('saved-model')).toBeTruthy())
  })
})
