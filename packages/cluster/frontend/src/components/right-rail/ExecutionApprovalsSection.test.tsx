// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import { ApprovalCard } from './ExecutionApprovalsSection'
import type { ExecutionApproval } from '@/hooks/useExecutionApprovals'

const approval: ExecutionApproval = {
  id: 'approval', execution_id: 'run', operating_room_id: 'room', task_id: 'task', task_room_id: 'room',
  task_title: 'Send reviewed report', input_revision: 1, source_task_id: 'source', source_result_id: 'result',
  source_result_version: 1, source_result_sha256: 'abc', artifact_id: 'file', artifact_room_id: 'room',
  artifact_url: '/api/v1/files/file', artifact_filename: 'report.md', artifact_sha256: 'def',
  action_kind: 'submission', target_alias: 'internal', target_label: 'Review inbox', target_url: 'https://example.test/review',
  summary: 'Send only this reviewed report', status: 'pending', can_decide: true, is_current: true,
  created_at: '2026-10-01T00:00:00Z',
}
afterEach(cleanup)
describe('compact approval disclosure', () => {
  it('keeps the destination and file in the summary and details before the decision', async () => {
    const decide = vi.fn().mockResolvedValue({ approval, error: null })
    render(<MemoryRouter><ApprovalCard compact approval={approval} decide={decide} /></MemoryRouter>)
    const details = screen.getByTestId('execution-approval-approval').querySelector('details')!
    expect(details.open).toBe(false)
    expect(details.querySelector('summary')).toHaveTextContent('Review inbox · report.md')
    details.open = true
    const button = screen.getByTestId('execution-approval-approve-approval')
    expect(screen.getByText('https://example.test/review').compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('Send only this reviewed report')).toBeInTheDocument()
    fireEvent.click(button)
    await waitFor(() => expect(decide).toHaveBeenCalledWith('approval', 'approve'))
  })
  it.each([{ can_decide: false }, { is_current: false }])('does not expose decisions when disallowed: %j', changes => {
    render(<MemoryRouter><ApprovalCard compact approval={{ ...approval, ...changes }} decide={vi.fn()} /></MemoryRouter>)
    expect(screen.queryByTestId('execution-approval-approve-approval')).not.toBeInTheDocument()
    expect(screen.queryByTestId('execution-approval-reject-approval')).not.toBeInTheDocument()
  })
})
