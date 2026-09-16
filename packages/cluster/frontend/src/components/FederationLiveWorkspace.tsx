import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  Ban,
  Check,
  CircleDashed,
  Link2,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
  UserRound,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { ParticipantView } from '@/lib/federationApi'
import { uuid } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'

type Federation = ReturnType<typeof useFederation>

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-[var(--color-danger)]">
      Last action failed · {error}
    </p>
  )
}

function Code({ children }: { children: string }) {
  return (
    <code className="rounded bg-[var(--color-surface-alt)] px-1.5 py-0.5 font-mono text-xs">
      {children}
    </code>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-[var(--color-foreground-muted)]">{label}</span>
      {children}
    </label>
  )
}

function principalLabel(participant: ParticipantView): string {
  const { principal } = participant
  const origin = principal.kind === 'human' ? 'human' : 'agent'
  return `${origin} · ${principal.node_id.slice(0, 8)}… · ${principal.principal_id.slice(0, 8)}…`
}

/**
 * #593 live workspace (task #33). Same three-step surface as the Phase 0
 * mock, but every card is fed by the real node/shared-channel endpoints via
 * ``useFederation``. Where the backend does not expose a surface yet the UI
 * says so instead of inventing data:
 *
 * - sharing not wired on this node → banner, forms disabled (#34);
 * - delegation state detail (accepted vs running, cancel vs unknown) →
 *   submission receipts + task status only until #35 lands its read API;
 * - channel/command routing fields are manual until bindings listing ships.
 */
export default function FederationLiveWorkspace({ federation }: { federation: Federation }) {
  const [tab, setTab] = useState('nodes')
  const {
    isAdmin,
    invites,
    peers,
    nodesCapability,
    channelRef,
    setChannelRef,
    snapshot,
    snapshotError,
    roster,
    submissions,
    busy,
    actionError,
  } = federation

  const commandFieldsReady = Boolean(channelRef?.senderNodeId && channelRef?.grantEpoch)
  const pendingSubmissions = submissions.filter((item) => item.state === 'unconfirmed')

  const statusLine = useMemo(() => {
    if (nodesCapability === 'disabled') {
      return 'Sharing is not enabled on this node yet — the federation services are not wired into this server build.'
    }
    if (nodesCapability === 'ready') return 'Node API reachable.'
    return 'Node API state unknown — retry after refreshing.'
  }, [nodesCapability])

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div
        role="status"
        className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-brand)]/20 bg-[var(--color-brand-tint-bg)] px-4 py-3 text-sm text-[var(--color-brand-tint-text)]"
      >
        <Radio className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div>
          <span className="font-semibold">Live federation view · real API</span>
          <span className="ml-2">
            Actions on this page call this node over HTTP with your admin session.
          </span>
        </div>
      </div>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-caption text-[var(--color-foreground-muted)]">Federated collaboration</p>
          <h1 className="mt-1 text-title">Work together across trusted nodes</h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--color-foreground-muted)]">
            Connect one node, choose exactly what to share, and keep task ownership separate from execution.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-label="Node capability">
          <Badge variant={nodesCapability === 'ready' ? 'default' : 'outline'}>{statusLine.split(' — ')[0]}</Badge>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void federation.refreshNodes()}
            disabled={busy || !isAdmin}
          >
            <RefreshCw className="mr-1 size-3" aria-hidden="true" /> Refresh
          </Button>
        </div>
      </header>

      {nodesCapability === 'disabled' && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <strong>Sharing disabled on this node.</strong>{' '}
            <span>
              The product build does not mount the federation services yet (app wiring is tracked
              separately). Everything below will start working the moment this node exposes{' '}
              <Code>/api/v1/node</Code> and <Code>/api/v1/shared-channels</Code>.
            </span>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid h-auto w-full grid-cols-3 sm:w-fit">
          <TabsTrigger value="nodes">1. Connection</TabsTrigger>
          <TabsTrigger value="channel">2. Shared channel</TabsTrigger>
          <TabsTrigger value="task">3. Task handoff</TabsTrigger>
        </TabsList>

        <TabsContent value="nodes" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Trusted peers</CardTitle>
                <CardDescription>
                  Nodes that completed the two-part invitation handshake with this node.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {peers.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">No peers yet.</p>
                )}
                {peers.map((peer) => (
                  <div key={peer.node_id} className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 font-medium">
                        <Server className="size-4" aria-hidden="true" />
                        <span className="truncate font-mono text-sm">{peer.node_id}</span>
                      </p>
                      <p className="mt-1 truncate text-xs text-[var(--color-foreground-muted)]">
                        pin {peer.fingerprint.slice(0, 16)}… · epoch {peer.certificate_epoch} · {peer.state}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => void federation.revokePeer(peer.node_id)}
                    >
                      Revoke
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Invitations</CardTitle>
                <CardDescription>
                  One-time bundles. A pending invitation grants nothing until the invited node
                  redeems it.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {invites.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">No invitations.</p>
                )}
                {invites.map((invite) => (
                  <div key={invite.id} className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-sm">{invite.id}</p>
                      <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                        for {invite.intended_node_id} · {invite.state} · expires{' '}
                        {new Date(invite.expires_at).toLocaleString()}
                      </p>
                    </div>
                    {invite.state === 'pending' && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void federation.revokeInvite(invite.id)}
                      >
                        Revoke
                      </Button>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <InviteComposer federation={federation} />
          <InviteAcceptor federation={federation} />
        </TabsContent>

        <TabsContent value="channel" className="mt-4">
          <ChannelSelector federation={federation} />
          {snapshot && (
            <Card className="mt-4">
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle>Shared channel roster</CardTitle>
                    <CardDescription>
                      Authority {snapshot.authority_node_id} · applied seq {snapshot.applied_seq} ·{' '}
                      {snapshot.messages.length} recent messages
                    </CardDescription>
                  </div>
                  <Badge variant="outline">
                    <Link2 className="mr-1 size-3" aria-hidden="true" /> Shared
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 sm:grid-cols-2">
                  {roster.map((participant) => (
                    <div
                      key={`${participant.principal.node_id}:${participant.principal.principal_id}`}
                      className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-alt)]">
                          <UserRound className="size-4" aria-hidden="true" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate font-medium">{participant.role}</p>
                          <p className="truncate text-xs text-[var(--color-foreground-muted)]">
                            {principalLabel(participant)}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge variant={participant.active ? 'secondary' : 'destructive'}>
                          {participant.active ? `rev ${participant.revision}` : `tombstone · rev ${participant.revision}`}
                        </Badge>
                        {participant.active && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void federation.changeParticipant(
                                participant.principal,
                                false,
                                participant.role,
                                participant.revision,
                              )
                            }
                          >
                            Remove
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-4 flex items-start gap-2 text-sm text-[var(--color-foreground-muted)]">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  Roster is the authority&apos;s projection. Removing a participant hides them here
                  and keeps the tombstone revision; grants and execution permission are separate.
                </p>
              </CardContent>
            </Card>
          )}
          {snapshotError && (
            <div role="alert" className="mt-4 flex gap-3 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <div>
                Channel read failed · <Code>{snapshotError.code}</Code> ({snapshotError.status}).
                Check the UUIDs, and that your account can read the bound room.
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="task" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
            <TaskComposer federation={federation} messages={snapshot?.messages ?? []} />
            <Card>
              <CardHeader>
                <CardTitle className="text-lead">Submissions</CardTitle>
                <CardDescription>
                  Durable local queue. “unconfirmed” means the channel owner has not committed it
                  yet.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {submissions.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">Nothing submitted.</p>
                )}
                {submissions.map((submission) => (
                  <div key={submission.request_id} className="rounded-[var(--radius-md)] border p-3 text-sm">
                    <p className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs">{submission.kind}</span>
                      <Badge variant={submission.state === 'confirmed' ? 'default' : submission.state === 'failed' ? 'destructive' : 'outline'}>
                        {submission.state}
                      </Badge>
                    </p>
                    <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                      {submission.receipt
                        ? `receipt ${submission.receipt.state} · task ${submission.receipt.task_status} · process ${submission.receipt.process_state}`
                        : 'no receipt yet'}
                      {submission.error_code ? ` · ${submission.error_code}` : ''}
                    </p>
                  </div>
                ))}
                {pendingSubmissions.length > 0 && (
                  <Button size="sm" variant="outline" className="w-full" disabled={busy} onClick={() => void federation.sync()}>
                    <RefreshCw className="mr-1 size-3" aria-hidden="true" /> Sync now
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>
          <ErrorLine error={actionError} />
          <p className="mt-4 flex items-start gap-2 text-xs text-[var(--color-foreground-muted)]">
            <CircleDashed className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            Remote accept / start / result arrive from the executor node over peer transport and
            surface here as receipt updates. Fine-grained delegation state (accepted vs running,
            cancel-requested vs unknown) needs the delegation read API and is pending backend work.
          </p>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function InviteComposer({ federation }: { federation: Federation }) {
  const [bundle, setBundle] = useState<string | null>(null)
  const [form, setForm] = useState({
    intendedNode: '',
    certificatePem: '',
    endpointUrl: '',
    endpointIps: '',
    channelId: '',
    capabilities: 'channel.read,message.send,task.request',
  })
  const disabled =
    federation.nodesCapability !== 'ready' ||
    !form.intendedNode ||
    !form.certificatePem ||
    !form.endpointUrl ||
    !form.endpointIps ||
    !form.channelId

  async function submit() {
    const created = await federation.createInvite({
      intended_node_id: form.intendedNode.trim(),
      certificate_pem: form.certificatePem.trim(),
      endpoint: {
        url: form.endpointUrl.trim(),
        approved_ips: form.endpointIps
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
      },
      scopes: [
        {
          channel_id: form.channelId.trim(),
          actors: [
            {
              node_id: form.intendedNode.trim(),
              kind: 'human',
              principal_id: '00000000-0000-0000-0000-000000000000',
            },
          ],
          capabilities: form.capabilities
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
          role: 'member',
        },
      ],
    })
    if (created) {
      setBundle(JSON.stringify(created, null, 2))
      setForm((current) => ({ ...current, certificatePem: '' }))
    }
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Create invitation</CardTitle>
        <CardDescription>
          Generates a one-time bundle for the intended node&apos;s admin. The token is shown once.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Intended node ID (UUID)">
            <Input value={form.intendedNode} onChange={(e) => setForm({ ...form, intendedNode: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Channel ID (UUID)">
            <Input value={form.channelId} onChange={(e) => setForm({ ...form, channelId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Peer endpoint URL">
            <Input value={form.endpointUrl} onChange={(e) => setForm({ ...form, endpointUrl: e.target.value })} placeholder="https://peer.example:port" />
          </Field>
          <Field label="Approved IPs (comma separated)">
            <Input value={form.endpointIps} onChange={(e) => setForm({ ...form, endpointIps: e.target.value })} placeholder="203.0.113.10" />
          </Field>
        </div>
        <Field label="Intended node certificate (PEM)">
          <textarea
            className="min-h-20 w-full rounded-[var(--radius-md)] border p-2 font-mono text-xs"
            value={form.certificatePem}
            onChange={(e) => setForm({ ...form, certificatePem: e.target.value })}
            placeholder="-----BEGIN CERTIFICATE-----"
          />
        </Field>
        <Field label="Capabilities (comma separated)">
          <Input value={form.capabilities} onChange={(e) => setForm({ ...form, capabilities: e.target.value })} />
        </Field>
        <div className="flex justify-end">
          <Button disabled={disabled || federation.busy} onClick={() => void submit()}>
            Create invitation
          </Button>
        </div>
        {bundle && (
          <div className="space-y-2">
            <p className="text-sm font-semibold">Bundle — copy now, it will not be shown again</p>
            <pre className="max-h-64 overflow-auto rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 font-mono text-xs">{bundle}</pre>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function InviteAcceptor({ federation }: { federation: Federation }) {
  const [form, setForm] = useState({ bundleJson: '', endpointUrl: '', endpointIps: '' })
  const [accepted, setAccepted] = useState(false)
  const disabled = !form.bundleJson || !form.endpointUrl || !form.endpointIps

  async function submit() {
    try {
      const bundle = JSON.parse(form.bundleJson)
      const result = await federation.acceptInvite({
        bundle,
        issuer_endpoint: {
          url: form.endpointUrl.trim(),
          approved_ips: form.endpointIps
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
        },
      })
      if (result !== null) setAccepted(true)
    } catch {
      setAccepted(false)
    }
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Accept invitation</CardTitle>
        <CardDescription>
          Paste a bundle you received from another node plus your independently approved endpoint
          for that node.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field label="Bundle JSON">
          <textarea
            className="min-h-24 w-full rounded-[var(--radius-md)] border p-2 font-mono text-xs"
            value={form.bundleJson}
            onChange={(e) => setForm({ ...form, bundleJson: e.target.value })}
            placeholder='{"protocol_version":1,"invite_id":…}'
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Issuer endpoint URL">
            <Input value={form.endpointUrl} onChange={(e) => setForm({ ...form, endpointUrl: e.target.value })} />
          </Field>
          <Field label="Approved IPs (comma separated)">
            <Input value={form.endpointIps} onChange={(e) => setForm({ ...form, endpointIps: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center justify-between">
          {accepted && <p className="flex items-center gap-1 text-sm text-[var(--color-success)]"><Check className="size-4" aria-hidden="true" /> Accepted — grants are now active.</p>}
          <Button className="ml-auto" variant="outline" disabled={disabled || federation.busy} onClick={() => void submit()}>
            Accept invitation
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function ChannelSelector({ federation }: { federation: Federation }) {
  const { channelRef, setChannelRef } = federation
  const [form, setForm] = useState({
    authority: channelRef?.authority ?? '',
    channel: channelRef?.channel ?? '',
    senderNodeId: channelRef?.senderNodeId ?? '',
    grantEpoch: channelRef?.grantEpoch ? String(channelRef.grantEpoch) : '',
    localRoomId: '',
  })
  const disabled = !form.authority || !form.channel

  function apply() {
    setChannelRef({
      authority: form.authority.trim(),
      channel: form.channel.trim(),
      senderNodeId: form.senderNodeId.trim() || undefined,
      grantEpoch: form.grantEpoch ? Number(form.grantEpoch) : undefined,
    })
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>Select shared channel</CardTitle>
            <CardDescription>
              Channel discovery is pending backend work — enter the UUIDs you received from the
              channel owner.
            </CardDescription>
          </div>
          <Button size="sm" variant="outline" disabled={federation.busy} onClick={() => void federation.sync()}>
            <RefreshCw className="mr-1 size-3" aria-hidden="true" /> Sync
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Authority node ID (UUID)">
            <Input value={form.authority} onChange={(e) => setForm({ ...form, authority: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Channel ID (UUID)">
            <Input value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="This node&apos;s ID — for task commands (follower side)">
            <Input value={form.senderNodeId} onChange={(e) => setForm({ ...form, senderNodeId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Grant epoch — for task commands">
            <Input value={form.grantEpoch} onChange={(e) => setForm({ ...form, grantEpoch: e.target.value })} placeholder="1" inputMode="numeric" />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <Field label="Bind a local room (authority side)">
            <Input value={form.localRoomId} onChange={(e) => setForm({ ...form, localRoomId: e.target.value })} placeholder="local room uuid — optional" />
          </Field>
          <Button
            variant="outline"
            disabled={!form.localRoomId || !form.authority || !form.channel || federation.busy}
            onClick={() =>
              void federation.bindChannel({
                authority_node_id: form.authority.trim(),
                channel_id: form.channel.trim(),
                local_room_id: form.localRoomId.trim(),
              })
            }
          >
            Bind
          </Button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[var(--color-foreground-muted)]">
            {federation.channelRef
              ? `Selected ${federation.channelRef.authority.slice(0, 8)}… / ${federation.channelRef.channel.slice(0, 8)}…`
              : 'No channel selected.'}
          </p>
          <Button disabled={disabled || federation.busy} onClick={apply}>
            Use channel
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function TaskComposer({
  federation,
  messages,
}: {
  federation: Federation
  messages: { message_id: string; text: string }[]
}) {
  const { channelRef } = federation
  const [form, setForm] = useState({
    executorNodeId: '',
    executorAgentId: '',
    taskId: '',
    delegationId: '',
    sourceMessageId: messages[0]?.message_id ?? '',
    cancelDelegationId: '',
    cancelRevision: '0',
  })
  const commandFieldsReady = Boolean(channelRef?.senderNodeId && channelRef?.grantEpoch)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Request remote execution</CardTitle>
        <CardDescription>
          Submit a <code className="font-mono text-xs">task.request</code> to the channel owner.
          It is queued durably and stays “unconfirmed” until the owner commits it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!commandFieldsReady && (
          <div role="status" className="rounded-[var(--radius-md)] border bg-[var(--color-surface-alt)] p-3 text-sm">
            Set <strong>this node ID</strong> and <strong>grant epoch</strong> in the Shared
            channel tab first — the command path needs both.
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Executor node ID (UUID)">
            <Input value={form.executorNodeId} onChange={(e) => setForm({ ...form, executorNodeId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Executor agent ID (UUID)">
            <Input value={form.executorAgentId} onChange={(e) => setForm({ ...form, executorAgentId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Task ID (UUID)">
            <Input value={form.taskId} onChange={(e) => setForm({ ...form, taskId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label="Delegation ID (UUID — blank to generate)">
            <Input value={form.delegationId} onChange={(e) => setForm({ ...form, delegationId: e.target.value })} placeholder="auto" />
          </Field>
        </div>
        <Field label="Source message">
          <select
            className="w-full rounded-[var(--radius-md)] border bg-white p-2 text-sm"
            value={form.sourceMessageId}
            onChange={(e) => setForm({ ...form, sourceMessageId: e.target.value })}
          >
            <option value="">— select a channel message —</option>
            {messages.map((message) => (
              <option key={message.message_id} value={message.message_id}>
                {message.text.slice(0, 60)}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex justify-end">
          <Button
            disabled={
              !commandFieldsReady ||
              !form.executorNodeId ||
              !form.executorAgentId ||
              !form.taskId ||
              !form.sourceMessageId ||
              federation.busy
            }
            onClick={() =>
              void federation.requestTask({
                delegationId: form.delegationId.trim() || uuid(),
                taskId: form.taskId.trim(),
                sourceMessageId: form.sourceMessageId,
                executorNodeId: form.executorNodeId.trim(),
                executorAgentId: form.executorAgentId.trim(),
              })
            }
          >
            Submit task request
          </Button>
        </div>
        <div className="rounded-[var(--radius-md)] border p-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            <Ban className="size-4" aria-hidden="true" /> Request cancellation
          </p>
          <div className="mt-2 grid gap-3 sm:grid-cols-[minmax(0,1fr)_110px_auto] sm:items-end">
            <Field label="Delegation ID (UUID)">
              <Input value={form.cancelDelegationId} onChange={(e) => setForm({ ...form, cancelDelegationId: e.target.value })} />
            </Field>
            <Field label="Expected revision">
              <Input value={form.cancelRevision} onChange={(e) => setForm({ ...form, cancelRevision: e.target.value })} inputMode="numeric" />
            </Field>
            <Button
              variant="destructive"
              disabled={!commandFieldsReady || !form.cancelDelegationId || federation.busy}
              onClick={() =>
                void federation.cancelTask({
                  delegationId: form.cancelDelegationId.trim(),
                  expectedRevision: Number(form.cancelRevision) || 0,
                })
              }
            >
              Request stop
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
