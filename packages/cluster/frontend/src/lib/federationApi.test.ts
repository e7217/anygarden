// @vitest-environment jsdom
// Contract tests for the federation API client (#593 / task #33). Locks the
// exact paths, methods and error-code parsing against the backend routers —
// a drifting path here is exactly the class of bug the integration review
// would otherwise catch late.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  FederationApiError,
  acceptInvite,
  changeParticipant,
  createInvite,
  getSnapshot,
  getSubmission,
  listInvites,
  listPeers,
  revokeGrant,
  revokeInvite,
  revokePeer,
  setPublication,
  submitCommand,
  syncChannel,
} from './federationApi'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.setItem('anygarden_token', 'test-token')
})

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

function lastCall(): { url: string; init: RequestInit | undefined } {
  const mock = vi.mocked(globalThis.fetch).mock
  const call = mock.calls.at(-1)!
  return { url: String(call[0]), init: call[1] }
}

describe('federationApi', () => {
  it('lists peers with admin auth header', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse([{ node_id: 'n1', fingerprint: 'f'.repeat(64), state: 'active', certificate_epoch: 2 }]))
    const peers = await listPeers()
    expect(peers).toHaveLength(1)
    const { url, init } = lastCall()
    expect(url).toBe('/api/v1/node/peers')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer test-token')
  })

  it('surfaces channel error codes from {"code": …} bodies', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ code: 'SHARING_DISABLED' }, 503))
    await expect(listInvites()).rejects.toMatchObject({
      code: 'SHARING_DISABLED',
      status: 503,
    })
    await expect(listInvites()).rejects.toBeInstanceOf(FederationApiError)
  })

  it('maps 404 on the admin router to HTTP_404 (not yet mounted)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('Not Found', { status: 404 }))
    await expect(listPeers()).rejects.toMatchObject({ code: 'HTTP_404', status: 404 })
  })

  it('treats 204 as success without parsing', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    await expect(revokeInvite('i-1')).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/node/invites/i-1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('creates invites with the closed InviteCreate shape', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ protocol_version: 1, invite_id: 'iv', token: 't'.repeat(40) }))
    await createInvite({
      intended_node_id: 'n2',
      certificate_pem: '---PEM---',
      endpoint: { url: 'https://peer', approved_ips: ['203.0.113.9'] },
      scopes: [
        {
          channel_id: 'c1',
          actors: [{ node_id: 'n2', kind: 'human', principal_id: 'p1' }],
          capabilities: ['channel.read'],
          role: 'observer',
        },
      ],
    })
    const { url, init } = lastCall()
    expect(url).toBe('/api/v1/node/invites')
    const body = JSON.parse(String(init?.body))
    expect(body).toMatchObject({
      intended_node_id: 'n2',
      endpoint: { url: 'https://peer', approved_ips: ['203.0.113.9'] },
      protocol_version: 1,
    })
  })

  it('accepts an invitation bundle at the invite-scoped path', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ ok: true }))
    await acceptInvite({
      bundle: {
        protocol_version: 1,
        invite_id: 'iv',
        issuer_node_id: 'n1',
        issuer_certificate_pem: 'x',
        intended_node_id: 'n2',
        intended_fingerprint: 'f'.repeat(64),
        token: 't'.repeat(40),
        scopes: [],
        expires_at: '2026-01-01T00:00:00Z',
        grant_expires_at: '2026-01-02T00:00:00Z',
      },
      issuer_endpoint: { url: 'https://peer', approved_ips: ['203.0.113.9'] },
    })
    expect(lastCall().url).toBe('/api/v1/node/invites/iv/accept')
  })

  it('reads a snapshot with after_seq and limit cursors', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ authority_node_id: 'a', channel_id: 'c', applied_seq: 7, messages: [], participants: [] }))
    const view = await getSnapshot('a', 'c', 3, 100)
    expect(view.applied_seq).toBe(7)
    expect(lastCall().url).toBe('/api/v1/shared-channels/a/c?after_seq=3&limit=100')
  })

  it('submits commands to the durable local queue', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ request_id: 'r1', state: 'unconfirmed', receipt: null, error_code: null }))
    const submission = await submitCommand({
      protocol_version: 1,
      request_id: 'r1',
      sender_node_id: 'n2',
      authority_node_id: 'n1',
      channel_id: 'c',
      grant_epoch: 1,
      actor: { node_id: 'n2', kind: 'human', principal_id: 'u1' },
      kind: 'task.request',
      payload: {},
    })
    expect(submission.state).toBe('unconfirmed')
    expect(lastCall().url).toBe('/api/v1/shared-channels/commands')
  })

  it('polls a single submission status', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(jsonResponse({ request_id: 'r1', state: 'confirmed', receipt: { state: 'applied', task_status: 'todo', process_state: 'not_started', revision: 1, seq: 2 }, error_code: null }))
    await getSubmission('a', 'c', 'r1')
    expect(lastCall().url).toBe('/api/v1/shared-channels/a/c/submissions/r1')
  })

  it('changes participants with an operation id and revision fence', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({}))
    await changeParticipant({
      channelId: 'c',
      principal: { node_id: 'n2', kind: 'agent', principal_id: 'p1' },
      active: false,
      role: 'member',
      expected_revision: 4,
    })
    const { url, init } = lastCall()
    expect(url).toBe('/api/v1/shared-channels/c/participants')
    const body = JSON.parse(String(init?.body))
    expect(body.expected_revision).toBe(4)
    expect(typeof body.operation_id).toBe('string')
  })

  it('sets publication state on the authority-owned path', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ active: false }))
    await setPublication('c', { node_id: 'n2', kind: 'human', principal_id: 'p1' }, false)
    const { url, init } = lastCall()
    expect(url).toBe('/api/v1/shared-channels/c/publication')
    expect(init?.method).toBe('PUT')
  })

  it('revokes peers and grants at their scoped paths', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    await revokePeer('n2')
    expect(lastCall().url).toBe('/api/v1/node/peers/n2')
    await revokeGrant('n2', 'c')
    expect(lastCall().url).toBe('/api/v1/node/peers/n2/grants/c')
  })

  it('triggers a sync pull with protocol version', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ events: [] }))
    await syncChannel({
      sender_node_id: 'n2',
      authority_node_id: 'n1',
      channel_id: 'c',
      grant_epoch: 1,
      actor: { node_id: 'n2', kind: 'human', principal_id: 'u1' },
    })
    const body = JSON.parse(String(lastCall().init?.body))
    expect(body.protocol_version).toBe(1)
  })
})

describe('uuid helper', () => {
  it('produces RFC 4122 v4 UUIDs without the secure-context API', async () => {
    const { uuid } = await import('./federationApi')
    const original = globalThis.crypto
    const shim = {
      ...original,
      randomUUID: undefined,
      getRandomValues: (view: Uint8Array) => {
        for (let i = 0; i < view.length; i += 1) view[i] = (i * 37 + 11) & 0xff
        return view
      },
    }
    Object.defineProperty(globalThis, 'crypto', { value: shim, configurable: true })
    try {
      const value = uuid()
      expect(value).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      )
    } finally {
      Object.defineProperty(globalThis, 'crypto', { value: original, configurable: true })
    }
  })
})

describe('task #35/#34 read surfaces', () => {
  it('lists bindings at the admin-only metadata path', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse([
        {
          authority_node_id: 'a',
          channel_id: 'c',
          local_room_id: 'r1',
          last_seq: 5,
          applied_seq: 4,
        },
      ]),
    )
    const { listBindings } = await import('./federationApi')
    const bindings = await listBindings()
    expect(bindings[0].local_room_id).toBe('r1')
    expect(lastCall().url).toBe('/api/v1/shared-channels/bindings')
  })

  it('reads delegation mirrors through the bound local room', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse([
        {
          authority_node_id: 'a',
          channel_id: 'c',
          delegation_id: 'd1',
          task_id: 't1',
          source_message_id: 'm1',
          requester: { node_id: 'a', kind: 'human', principal_id: 'u1' },
          executor: { node_id: 'b', agent_id: 'g1' },
          execution_id: null,
          revision: 2,
          state: 'running',
          process_state: 'running',
          task_status: 'in_progress',
        },
      ]),
    )
    const { listDelegations } = await import('./federationApi')
    const delegations = await listDelegations('r1')
    expect(delegations[0].state).toBe('running')
    expect(lastCall().url).toBe('/api/v1/rooms/r1/delegations')
  })
})
