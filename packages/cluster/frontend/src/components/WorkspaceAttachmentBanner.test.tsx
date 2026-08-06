// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it } from 'vitest'

import WorkspaceAttachmentBanner from './WorkspaceAttachmentBanner'

afterEach(() => cleanup())

describe('WorkspaceAttachmentBanner', () => {
  it('renders nothing without an active attachment', () => {
    const { container } = render(<WorkspaceAttachmentBanner attachments={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows only the redacted label, mode and audit signal', () => {
    render(
      <WorkspaceAttachmentBanner
        attachments={[
          {
            id: 'attachment-1',
            workspace_id: 'ws_opaque',
            label: 'customer docs',
            agent_id: 'agent-1',
            mode: 'write',
            epoch: 4,
            expires_at: '2030-01-01T00:00:00Z',
          },
        ]}
      />,
    )
    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('External workspace attached')
    expect(banner).toHaveTextContent('customer docs · scoped write · audited')
    expect(banner).not.toHaveTextContent('ws_opaque')
  })
})
