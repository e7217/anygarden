// @vitest-environment jsdom
// #839 — connecting two nodes with a connection card and an invite code.
import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import {
  CertificateExpiry,
  ConnectionError,
  certificateStatus,
  InviteAcceptor,
  InviteComposer,
  NodeConnectionCard,
} from './FederationConnectionSetup'
import type { ConnectionCardPreview, InviteCodePreview } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'

type Federation = ReturnType<typeof useFederation>

const FINGERPRINT = 'ab12'.repeat(16)
const PEER = '22222222-2222-4222-8222-222222222222'
const ISSUER = '11111111-1111-4111-8111-111111111111'
const CHANNEL = '33333333-3333-4333-8333-333333333333'

function federation(overrides: Partial<Federation> = {}): Federation {
  return {
    nodesCapability: 'ready',
    busy: false,
    actionError: null,
    nodeIdentity: {
      node_id: ISSUER,
      fingerprint: 'cd34'.repeat(16),
      certificate_expires_at: '2027-01-13T00:00:00Z',
      public_url: 'https://alpha.example:8451',
    },
    createConnectionCard: vi.fn(async () => ({
      card: 'agpeer1.card',
      node_id: ISSUER,
      fingerprint: 'cd34'.repeat(16),
      url: 'https://alpha.example:8451',
    })),
    inspectConnectionCard: vi.fn(),
    inspectInviteCode: vi.fn(),
    createInvite: vi.fn(async () => ({ invite_code: 'aginvite1.code' })),
    acceptInvite: vi.fn(async () => ({ state: 'accepted' })),
    ...overrides,
  } as unknown as Federation
}

function cardPreview(overrides: Partial<ConnectionCardPreview> = {}): ConnectionCardPreview {
  return {
    node_id: PEER,
    fingerprint: FINGERPRINT,
    certificate_pem: '-----BEGIN CERTIFICATE-----',
    certificate_expires_at: '2027-01-13T00:00:00Z',
    url: 'https://beta.example:8451',
    suggested_ips: ['203.0.113.7'],
    requires_private: false,
    ...overrides,
  }
}

function invitePreview(overrides: Partial<InviteCodePreview> = {}): InviteCodePreview {
  return {
    bundle: {
      protocol_version: 1,
      invite_id: 'iv',
      issuer_node_id: ISSUER,
      issuer_certificate_pem: 'pem',
      intended_node_id: PEER,
      intended_fingerprint: FINGERPRINT,
      token: 't'.repeat(43),
      scopes: [],
      expires_at: '2026-10-15T10:00:00Z',
      grant_expires_at: '2026-10-16T10:00:00Z',
    },
    issuer_node_id: ISSUER,
    issuer_fingerprint: FINGERPRINT,
    issuer_url: 'https://192.168.1.5:8451',
    suggested_ips: ['192.168.1.5'],
    requires_private: true,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('NodeConnectionCard', () => {
  it('[FED-11] shows this node’s identity and creates a card for the configured address', async () => {
    const fed = federation()
    render(<NodeConnectionCard federation={fed} />)

    expect(screen.getByText(ISSUER)).toBeInTheDocument()
    expect(screen.getByText(/cd34 cd34/)).toBeInTheDocument()
    expect(screen.getByLabelText(/This node’s address for peers/)).toHaveValue('https://alpha.example:8451')

    fireEvent.click(screen.getByRole('button', { name: 'Create card' }))
    await waitFor(() => expect(fed.createConnectionCard).toHaveBeenCalledWith('https://alpha.example:8451'))
    expect(await screen.findByDisplayValue('agpeer1.card')).toBeInTheDocument()
  })

  it('needs an address before a card can be created', () => {
    const fed = federation({
      nodeIdentity: { ...federation().nodeIdentity!, public_url: null },
    } as Partial<Federation>)
    render(<NodeConnectionCard federation={fed} />)
    expect(screen.getByRole('button', { name: 'Create card' })).toBeDisabled()
  })
})

describe('InviteComposer', () => {
  async function reviewCard(fed: Federation) {
    render(<InviteComposer federation={fed} />)
    fireEvent.change(screen.getByLabelText('Other node’s connection card'), {
      target: { value: 'agpeer1.peer' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Review/ }))
    await screen.findByText(PEER)
  }

  it('[FED-12] cannot create the invitation until the fingerprint is confirmed', async () => {
    const fed = federation({ inspectConnectionCard: vi.fn(async () => cardPreview()) } as Partial<Federation>)
    await reviewCard(fed)

    expect(fed.inspectConnectionCard).toHaveBeenCalledWith('agpeer1.peer')
    expect(screen.getByText(/ab12 ab12/)).toBeInTheDocument()
    expect(screen.getByLabelText(/IPs to allow/)).toHaveValue('203.0.113.7')
    fireEvent.change(screen.getByLabelText(/Channel ID/), { target: { value: CHANNEL } })
    const create = screen.getByRole('button', { name: 'Create invitation' })
    expect(create).toBeDisabled()

    fireEvent.click(screen.getByLabelText(/compared this fingerprint/))
    expect(create).toBeEnabled()
    fireEvent.click(create)

    await waitFor(() => expect(fed.createInvite).toHaveBeenCalled())
    const input = vi.mocked(fed.createInvite).mock.calls[0][0]
    expect(input).toMatchObject({
      intended_node_id: PEER,
      certificate_pem: '-----BEGIN CERTIFICATE-----',
      endpoint: { url: 'https://beta.example:8451', approved_ips: ['203.0.113.7'], allow_private: false },
      issuer_url: 'https://alpha.example:8451',
    })
    expect(input.scopes[0].channel_id).toBe(CHANNEL)
    expect(await screen.findByDisplayValue('aginvite1.code')).toBeInTheDocument()
    expect(screen.getByText(/one-time secret/)).toBeInTheDocument()
  })

  it('[FED-11] private addresses are sent only after explicitly allowing them', async () => {
    const fed = federation({
      inspectConnectionCard: vi.fn(async () =>
        cardPreview({ url: 'https://10.0.0.8:8451', suggested_ips: ['10.0.0.8'], requires_private: true }),
      ),
    } as Partial<Federation>)
    await reviewCard(fed)

    expect(screen.getByText(/suggested IPs are private network addresses/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Channel ID/), { target: { value: CHANNEL } })
    fireEvent.click(screen.getByLabelText('Allow private network addresses'))
    fireEvent.click(screen.getByLabelText(/compared this fingerprint/))
    fireEvent.click(screen.getByRole('button', { name: 'Create invitation' }))

    await waitFor(() => expect(fed.createInvite).toHaveBeenCalled())
    expect(vi.mocked(fed.createInvite).mock.calls[0][0].endpoint).toEqual({
      url: 'https://10.0.0.8:8451',
      approved_ips: ['10.0.0.8'],
      allow_private: true,
    })
  })

  it('shows the error of its own action and reports when it starts one', async () => {
    const onAction = vi.fn()
    const fed = federation({ inspectConnectionCard: vi.fn(async () => cardPreview()) } as Partial<Federation>)
    render(<InviteComposer federation={fed} error="ENDPOINT_DENIED (400)" onAction={onAction} />)
    expect(screen.getByRole('alert').textContent).toMatch(/Allow private network addresses/)

    fireEvent.change(screen.getByLabelText('Other node’s connection card'), {
      target: { value: 'agpeer1.peer' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Review/ }))
    expect(onAction).toHaveBeenCalledTimes(1)
    await screen.findByText(PEER)
  })

  it('editing the pasted card discards the previous review', async () => {
    const fed = federation({ inspectConnectionCard: vi.fn(async () => cardPreview()) } as Partial<Federation>)
    await reviewCard(fed)
    fireEvent.change(screen.getByLabelText('Other node’s connection card'), {
      target: { value: 'agpeer1.other' },
    })
    expect(screen.queryByText(PEER)).not.toBeInTheDocument()
  })
})

describe('InviteAcceptor', () => {
  it('[FED-12] accepts the pasted code with the reviewed endpoint only after confirmation', async () => {
    const fed = federation({ inspectInviteCode: vi.fn(async () => invitePreview()) } as Partial<Federation>)
    render(<InviteAcceptor federation={fed} />)
    fireEvent.change(screen.getByLabelText('Invite code'), { target: { value: 'aginvite1.code' } })
    fireEvent.click(screen.getByRole('button', { name: /Review/ }))
    await screen.findByText(ISSUER)

    expect(screen.getByText('https://192.168.1.5:8451')).toBeInTheDocument()
    const accept = screen.getByRole('button', { name: 'Accept invitation' })
    expect(accept).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Allow private network addresses'))
    fireEvent.click(screen.getByLabelText(/compared this fingerprint/))
    fireEvent.click(accept)

    await waitFor(() => expect(fed.acceptInvite).toHaveBeenCalled())
    expect(vi.mocked(fed.acceptInvite).mock.calls[0][0]).toEqual({
      bundle: invitePreview().bundle,
      issuer_endpoint: {
        url: 'https://192.168.1.5:8451',
        approved_ips: ['192.168.1.5'],
        allow_private: true,
      },
    })
    expect(await screen.findByText(/Accepted/)).toBeInTheDocument()
  })
})

describe('ConnectionError', () => {
  it('explains the setup error codes and keeps the wire code', () => {
    render(<ConnectionError error="ENDPOINT_DENIED (400)" />)
    const alert = screen.getByRole('alert')
    expect(alert.textContent).toMatch(/Allow private network addresses/)
    expect(alert.textContent).toContain('ENDPOINT_DENIED (400)')
  })

  it('falls back to the raw code for other errors', () => {
    render(<ConnectionError error="GRANT_DENIED (403)" />)
    expect(screen.getByRole('alert').textContent).toContain('GRANT_DENIED (403)')
  })
})

describe('certificate expiry (#842)', () => {
  const now = Date.parse('2026-10-15T00:00:00Z')

  it('classifies ok, soon (≤30 days) and expired', () => {
    expect(certificateStatus('2027-10-15T00:00:00Z', now)).toEqual({ kind: 'ok' })
    expect(certificateStatus('2026-11-14T12:00:00Z', now)).toEqual({ kind: 'soon', days: 30 })
    expect(certificateStatus('2026-10-20T00:00:00Z', now)).toEqual({ kind: 'soon', days: 5 })
    expect(certificateStatus('2026-10-14T00:00:00Z', now)).toEqual({ kind: 'expired' })
  })

  it('[FED-14] warns on this node’s card when its certificate expires within 30 days', () => {
    const soon = new Date(Date.now() + 10 * 24 * 60 * 60 * 1000 + 60_000).toISOString()
    render(
      <NodeConnectionCard
        federation={federation({
          nodeIdentity: { ...federation().nodeIdentity!, certificate_expires_at: soon },
        } as Partial<Federation>)}
      />,
    )
    expect(screen.getByText(/Expires in 10 days/)).toBeInTheDocument()
    expect(screen.getByText(/init-credentials --force/)).toBeInTheDocument()
  })

  it('shows only the date when renewal is not due', () => {
    render(<NodeConnectionCard federation={federation()} />)
    expect(screen.queryByText(/Expires in/)).not.toBeInTheDocument()
  })

  it('[FED-14] tells the admin when a peer certificate has expired', () => {
    render(<CertificateExpiry expiresAt="2020-01-01T00:00:00Z" owner="peer" />)
    expect(screen.getByText(/cannot be reached until its admin renews it/)).toBeInTheDocument()
  })
})
