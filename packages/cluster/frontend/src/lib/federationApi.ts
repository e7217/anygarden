/**
 * Federation API client (#593 / task #33).
 *
 * Typed wrapper over the real node-peer and shared-channel endpoints that
 * shipped with #590–#592 (main f2c4fea). Every call goes through ``apiFetch``
 * so the admin JWT travels with the request. Error codes are the closed
 * wire vocabulary of ``ChannelError``/``PeerError`` — the UI branches on
 * ``code`` (not on prose) for its disabled/pending states.
 *
 * Transport note: federation has no WebSocket push. The durable read path is
 * HTTP polling of the snapshot cursor (``after_seq``) plus the one-shot sync
 * trigger. Do not add long-lived sockets here without a backend contract.
 */
import { apiFetch } from './api'

export class FederationApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(`Federation API error ${status} ${code}`)
    this.name = 'FederationApiError'
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, init)
  if (response.status === 204) return undefined as T
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) {
    const code =
      body && typeof body === 'object' && 'code' in body
        ? String((body as { code: unknown }).code)
        : detailCode((body as { detail?: unknown } | null)?.detail) ??
          ('HTTP_' + response.status)
    throw new FederationApiError(code, response.status)
  }
  return body as T
}

/** FastAPI validation errors put a string (or list) in ``detail``. */
function detailCode(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  return null
}

function post(url: string, body: unknown): Promise<unknown> {
  return request(url, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * RFC 4122 v4 UUID. ``crypto.randomUUID`` is only available in secure
 * contexts, and this product is legitimately deployed on plain-HTTP LANs —
 * fall back to ``getRandomValues`` (always available) there.
 */
export function uuid(): string {
  const source = globalThis.crypto as Crypto | undefined
  if (source && typeof source.randomUUID === 'function') {
    return source.randomUUID()
  }
  const bytes = new Uint8Array(16)
  ;(source ?? { getRandomValues: mathRandomFill }).getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** Last-resort entropy for browsers with no WebCrypto at all. */
function mathRandomFill(view: Uint8Array): Uint8Array {
  for (let i = 0; i < view.length; i += 1) view[i] = Math.floor(Math.random() * 256)
  return view
}

function put(url: string, body: unknown): Promise<unknown> {
  return request(url, { method: 'PUT', body: JSON.stringify(body) })
}

function del(url: string): Promise<unknown> {
  return request(url, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// Wire types (closed schemas from contracts/federation/v1 + #591/#592)
// ---------------------------------------------------------------------------

export interface Principal {
  node_id: string
  kind: 'agent' | 'human'
  principal_id: string
}

export interface EndpointInput {
  url: string
  approved_ips: string[]
  allow_private?: boolean
}

export interface Scope {
  channel_id: string
  actors: Principal[]
  capabilities: string[]
  role: 'observer' | 'member' | 'admin'
}

export interface InviteBundleView {
  protocol_version: 1
  invite_id: string
  issuer_node_id: string
  issuer_certificate_pem: string
  intended_node_id: string
  intended_fingerprint: string
  /** Present exactly once on creation. Never re-echoed by the server. */
  token: string
  scopes: Scope[]
  expires_at: string
  grant_expires_at: string
}

export interface InviteView {
  id: string
  intended_node_id: string
  state: 'pending' | 'accepted' | 'expired' | 'revoked'
  expires_at: string
}

export interface PeerView {
  node_id: string
  fingerprint: string
  state: string
  certificate_epoch: number
}

export interface ParticipantView {
  principal: Principal
  active: boolean
  role: string
  revision: number
}

export interface FederationMessage {
  message_id: string
  authority_node_id: string
  channel_id: string
  actor: Principal
  seq: number
  thread_root_id: string | null
  confirmed: boolean
  text: string
}

export interface SnapshotView {
  authority_node_id: string
  channel_id: string
  applied_seq: number
  messages: FederationMessage[]
  participants: ParticipantView[]
}

/** Metadata-only binding view (#593 task #34 / PR608). */
export interface BindingView {
  authority_node_id: string
  channel_id: string
  local_room_id: string
  last_seq: number
  applied_seq: number
}

/**
 * One authority-confirmed delegation mirror (task #35 / PR607). state /
 * process_state / task_status carry the wire vocabulary unchanged
 * (requested, accepted, running, completed, failed, rejected,
 * cancel_requested, cancelled, unknown).
 */
export interface DelegationStatusView {
  authority_node_id: string
  channel_id: string
  delegation_id: string
  task_id: string
  source_message_id: string
  requester: Principal
  executor: { node_id: string; agent_id: string }
  execution_id: string | null
  revision: number
  state: string
  process_state: string
  task_status: string
}

export type SubmissionState =
  | 'unconfirmed'
  | 'confirmed'
  | 'failed'

export interface SubmissionView {
  request_id: string
  state: SubmissionState
  receipt: null | {
    state: string
    task_status: string
    process_state: string
    revision: number
    seq: number
  }
  error_code: string | null
}

export interface CommandEnvelope {
  protocol_version: 1
  request_id: string
  sender_node_id: string
  authority_node_id: string
  channel_id: string
  grant_epoch: number
  actor: Principal
  kind: string
  payload: Record<string, unknown>
}

// ---------------------------------------------------------------------------
// Node peers & invitations (admin)
// ---------------------------------------------------------------------------

export async function listInvites(): Promise<InviteView[]> {
  return request<InviteView[]>('/api/v1/node/invites')
}

export async function createInvite(input: {
  intended_node_id: string
  certificate_pem: string
  endpoint: EndpointInput
  scopes: Scope[]
  expires_in_seconds?: number
  grant_expires_in_seconds?: number
}): Promise<InviteBundleView> {
  return post('/api/v1/node/invites', {
    protocol_version: 1,
    ...input,
  }) as Promise<InviteBundleView>
}

export async function acceptInvite(input: {
  bundle: Omit<InviteBundleView, 'token'> & { token: string }
  issuer_endpoint: EndpointInput
}): Promise<unknown> {
  const bundle = { ...input.bundle, protocol_version: 1 }
  return post(`/api/v1/node/invites/${input.bundle.invite_id}/accept`, {
    bundle,
    issuer_endpoint: input.issuer_endpoint,
  })
}

export async function revokeInvite(inviteId: string): Promise<void> {
  await del(`/api/v1/node/invites/${inviteId}`)
}

export async function listPeers(): Promise<PeerView[]> {
  return request<PeerView[]>('/api/v1/node/peers')
}

export async function revokePeer(nodeId: string): Promise<unknown> {
  return del(`/api/v1/node/peers/${nodeId}`)
}

export async function rotateCertificate(
  nodeId: string,
  certificatePem: string,
  expectedPeerEpoch: number,
): Promise<unknown> {
  return post(`/api/v1/node/peers/${nodeId}/certificate`, {
    certificate_pem: certificatePem,
    expected_peer_epoch: expectedPeerEpoch,
  })
}

export async function replaceGrant(
  nodeId: string,
  channelId: string,
  input: { expected_grant_epoch: number; expires_in_seconds?: number },
): Promise<unknown> {
  return put(`/api/v1/node/peers/${nodeId}/grants/${channelId}`, input)
}

export async function revokeGrant(nodeId: string, channelId: string): Promise<unknown> {
  return del(`/api/v1/node/peers/${nodeId}/grants/${channelId}`)
}

// ---------------------------------------------------------------------------
// Shared channels (local node surface)
// ---------------------------------------------------------------------------

export async function createBinding(input: {
  authority_node_id: string
  channel_id: string
  local_room_id: string
}): Promise<{ authority_node_id: string; channel_id: string; local_room_id: string }> {
  return post('/api/v1/shared-channels/bindings', input) as Promise<{
    authority_node_id: string
    channel_id: string
    local_room_id: string
  }>
}

export async function setPublication(
  channelId: string,
  principal: Principal,
  active: boolean,
): Promise<{ active: boolean }> {
  return put(`/api/v1/shared-channels/${channelId}/publication`, {
    principal,
    active,
  }) as Promise<{ active: boolean }>
}

export async function changeParticipant(input: {
  channelId: string
  principal: Principal
  active: boolean
  role: string
  expected_revision: number
}): Promise<unknown> {
  const { channelId, ...body } = input
  return post(`/api/v1/shared-channels/${channelId}/participants`, {
    ...body,
    operation_id: uuid(),
  })
}

export async function submitCommand(envelope: CommandEnvelope): Promise<SubmissionView> {
  return post('/api/v1/shared-channels/commands', envelope) as Promise<SubmissionView>
}

export async function getSnapshot(
  authority: string,
  channel: string,
  afterSeq = 0,
  limit = 50,
): Promise<SnapshotView> {
  return request<SnapshotView>(
    `/api/v1/shared-channels/${authority}/${channel}?after_seq=${afterSeq}&limit=${limit}`,
  )
}

export async function getSubmission(
  authority: string,
  channel: string,
  requestId: string,
): Promise<SubmissionView> {
  return request<SubmissionView>(
    `/api/v1/shared-channels/${authority}/${channel}/submissions/${requestId}`,
  )
}

export async function retrySubmission(
  authority: string,
  channel: string,
  requestId: string,
): Promise<unknown> {
  return post(
    `/api/v1/shared-channels/${authority}/${channel}/submissions/${requestId}/retry`,
    {},
  )
}

export async function syncChannel(scope: {
  sender_node_id: string
  authority_node_id: string
  channel_id: string
  grant_epoch: number
  actor: Principal
}): Promise<unknown> {
  return post('/api/v1/shared-channels/sync', { protocol_version: 1, ...scope })
}

export async function listBindings(): Promise<BindingView[]> {
  return request<BindingView[]>('/api/v1/shared-channels/bindings')
}

export async function listDelegations(roomId: string): Promise<DelegationStatusView[]> {
  return request<DelegationStatusView[]>(`/api/v1/rooms/${roomId}/delegations`)
}
