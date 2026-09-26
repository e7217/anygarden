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
import type { BindingView, DelegationStatusView, ParticipantView } from '@/lib/federationApi'
import { uuid } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'
import { useLocale } from '@/i18n/LocaleProvider'

type Federation = ReturnType<typeof useFederation>

function ErrorLine({ error }: { error: string | null }) {
  const { t } = useLocale()
  if (!error) return null
  return (
    <p role="alert" className="text-sm text-[var(--color-danger)]">
      {t('federation.errorPrefix')} · {error}
    </p>
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

function principalLabel(participant: ParticipantView, origin: string): string {
  const { principal } = participant
  return `${origin} · ${principal.node_id.slice(0, 8)}… · ${principal.principal_id.slice(0, 8)}…`
}

/**
 * #593 live workspace (task #33). Same three-step surface as the Phase 0
 * mock, but every card is fed by the real node/shared-channel endpoints via
 * ``useFederation``. Where the backend does not expose a surface yet the UI
 * says so instead of inventing data:
 *
 * - sharing not wired on this node → banner, forms disabled (#34);
 * - delegation states are the authority's confirmed mirror projection,
 *   polled through the bound local room (task #35);
 * - channel selection uses the bindings listing when the node exposes it,
 *   with manual entry as fallback (task #34).
 */
export default function FederationLiveWorkspace({ federation }: { federation: Federation }) {
  const { t, formatDate } = useLocale()
  const [tab, setTab] = useState('nodes')
  const {
    isAdmin,
    invites,
    peers,
    nodesCapability,
    snapshot,
    snapshotError,
    roster,
    delegations,
    submissions,
    busy,
    actionError,
  } = federation

  const pendingSubmissions = submissions.filter((item) => item.state === 'unconfirmed')

  const statusLine = useMemo(() => {
    if (nodesCapability === 'disabled') {
      return t('federation.statusDisabled')
    }
    if (nodesCapability === 'ready') return t('federation.statusReady')
    return t('federation.statusUnknown')
  }, [nodesCapability, t])

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div
        role="status"
        className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-brand)]/20 bg-[var(--color-brand-tint-bg)] px-4 py-3 text-sm text-[var(--color-brand-tint-text)]"
      >
        <Radio className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div>
          <span className="font-semibold">{t('federation.liveNotice')}</span>
          <span className="ml-2">
            {t('federation.liveNoticeBody')}
          </span>
        </div>
      </div>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-caption text-[var(--color-foreground-muted)]">{t('federation.eyebrow')}</p>
          <h1 className="mt-1 text-heading">{t('federation.title')}</h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--color-foreground-muted)]">
            {t('federation.description')}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-label={t('federation.nodeCapability')}>
          <Badge variant={nodesCapability === 'ready' ? 'default' : 'outline'}>{statusLine}</Badge>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void federation.refreshNodes()}
            disabled={busy || !isAdmin}
          >
            <RefreshCw className="mr-1 size-3" aria-hidden="true" /> {t('common.refresh')}
          </Button>
        </div>
      </header>

      {nodesCapability === 'disabled' && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div>
            <strong>{t('federation.sharingDisabledTitle')}</strong>{' '}
            <span>{t('federation.sharingDisabledBody')}</span>
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid h-auto w-full grid-cols-3 sm:w-fit">
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="nodes">{t('federation.tabConnection')}</TabsTrigger>
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="channel">{t('federation.tabChannel')}</TabsTrigger>
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="task">{t('federation.tabTask')}</TabsTrigger>
        </TabsList>

        <TabsContent value="nodes" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>{t('federation.trustedPeers')}</CardTitle>
                <CardDescription>
                  {t('federation.trustedPeersDescription')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {peers.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.noPeers')}</p>
                )}
                {peers.map((peer) => (
                  <div key={peer.node_id} className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 font-medium">
                        <Server className="size-4" aria-hidden="true" />
                        <span className="truncate font-mono text-sm">{peer.node_id}</span>
                      </p>
                      <p className="mt-1 truncate text-xs text-[var(--color-foreground-muted)]">
                        {t('federation.pin')} {peer.fingerprint.slice(0, 16)}… · {t('federation.epoch')} {peer.certificate_epoch} · {peer.state}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busy}
                      onClick={() => void federation.revokePeer(peer.node_id)}
                    >
                      {t('federation.revoke')}
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t('federation.invitations')}</CardTitle>
                <CardDescription>
                  {t('federation.invitationsDescription')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {invites.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.noInvitations')}</p>
                )}
                {invites.map((invite) => (
                  <div key={invite.id} className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-sm">{invite.id}</p>
                      <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                        {t('federation.invitationFor', {
                          node: invite.intended_node_id,
                          state: invite.state,
                          date: formatDate(new Date(invite.expires_at), { dateStyle: 'medium', timeStyle: 'short' }),
                        })}
                      </p>
                    </div>
                    {invite.state === 'pending' && (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => void federation.revokeInvite(invite.id)}
                      >
                        {t('federation.revoke')}
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
                    <CardTitle>{t('federation.roster')}</CardTitle>
                    <CardDescription>
                      {t('federation.rosterSummary', {
                        authority: snapshot.authority_node_id,
                        sequence: snapshot.applied_seq,
                        count: snapshot.messages.length,
                      })}
                    </CardDescription>
                  </div>
                  <Badge variant="outline">
                    <Link2 className="mr-1 size-3" aria-hidden="true" /> {t('federation.shared')}
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
                            {principalLabel(participant, t(participant.principal.kind === 'human' ? 'federation.human' : 'federation.agent'))}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge variant={participant.active ? 'secondary' : 'destructive'}>
                          {participant.active
                            ? t('federation.revision', { revision: participant.revision })
                            : t('federation.tombstoneRevision', { revision: participant.revision })}
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
                            {t('federation.remove')}
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <p className="mt-4 flex items-start gap-2 text-sm text-[var(--color-foreground-muted)]">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  {t('federation.rosterNote')}
                </p>
              </CardContent>
            </Card>
          )}
          {snapshotError && (
            <div role="alert" className="mt-4 flex gap-3 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <div>
                {t('federation.channelReadFailed', { code: snapshotError.code, status: snapshotError.status })}
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="task" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-4">
              <DelegationList delegations={delegations} />
              <TaskComposer federation={federation} messages={snapshot?.messages ?? []} />
            </div>
            <Card>
              <CardHeader>
                <CardTitle className="text-lead">{t('federation.submissions')}</CardTitle>
                <CardDescription>
                  {t('federation.submissionsDescription')}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {submissions.length === 0 && (
                  <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.noSubmissions')}</p>
                )}
                {submissions.map((submission) => (
                  <div key={submission.request_id} className="rounded-[var(--radius-md)] border p-3 text-sm">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs">{submission.kind}</span>
                      <Badge variant={submission.state === 'confirmed' ? 'default' : submission.state === 'failed' ? 'destructive' : 'outline'}>
                        {submission.state}
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                      {submission.receipt
                        ? t('federation.receiptSummary', {
                          state: submission.receipt.state,
                          task: submission.receipt.task_status,
                          process: submission.receipt.process_state,
                        })
                        : t('federation.noReceipt')}
                      {submission.error_code ? ` · ${submission.error_code}` : ''}
                    </p>
                  </div>
                ))}
                {pendingSubmissions.length > 0 && (
                  <Button size="sm" variant="outline" className="w-full" disabled={busy} onClick={() => void federation.sync()}>
                    <RefreshCw className="mr-1 size-3" aria-hidden="true" /> {t('federation.syncNow')}
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>
          <ErrorLine error={actionError} />
          <p className="mt-4 flex items-start gap-2 text-xs text-[var(--color-foreground-muted)]">
            <CircleDashed className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {t('federation.remoteEventNote')}
          </p>
        </TabsContent>
      </Tabs>
    </div>
  )
}

const delegationStateTone: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  requested: 'outline',
  accepted: 'secondary',
  running: 'default',
  completed: 'default',
  failed: 'destructive',
  rejected: 'destructive',
  cancel_requested: 'outline',
  cancelled: 'destructive',
  unknown: 'destructive',
}

/**
 * Authority-confirmed delegation mirrors (task #35 endpoint), shown with the
 * wire vocabulary untouched so the UI never re-derives transitions.
 */
function DelegationList({ delegations }: { delegations: DelegationStatusView[] }) {
  const { t } = useLocale()
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{t('federation.delegations')}</CardTitle>
            <CardDescription>
              {t('federation.delegationsDescription')}
            </CardDescription>
          </div>
          <Badge variant="outline">{delegations.length}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {delegations.length === 0 && (
          <p className="text-sm text-[var(--color-foreground-muted)]">
            {t('federation.noDelegations')}
          </p>
        )}
        {delegations.map((delegation) => (
          <div key={delegation.delegation_id} className="rounded-[var(--radius-md)] border p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs">{delegation.delegation_id}</p>
              <Badge variant={delegationStateTone[delegation.state] ?? 'outline'}>
                {delegation.state}
              </Badge>
            </div>
            <p className="mt-2 text-[var(--color-foreground-muted)]">
              {t('federation.delegationSummary', {
                node: `${delegation.executor.node_id.slice(0, 8)}…`,
                agent: `${delegation.executor.agent_id.slice(0, 8)}…`,
                process: delegation.process_state.replace('_', ' '),
                task: delegation.task_status.replace('_', ' '),
                revision: delegation.revision,
              })}
            </p>
            {(delegation.state === 'cancel_requested' || delegation.state === 'unknown') && (
              <p className="mt-2 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-2 text-xs">
                {delegation.state === 'cancel_requested'
                  ? t('federation.stopPending')
                  : t('federation.outcomeUnknown')}
              </p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function InviteComposer({ federation }: { federation: Federation }) {
  const { t } = useLocale()
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
        <CardTitle>{t('federation.createInvitation')}</CardTitle>
        <CardDescription>
          {t('federation.createInvitationDescription')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('federation.intendedNodeId')}>
            <Input value={form.intendedNode} onChange={(e) => setForm({ ...form, intendedNode: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.channelId')}>
            <Input value={form.channelId} onChange={(e) => setForm({ ...form, channelId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.peerEndpointUrl')}>
            <Input value={form.endpointUrl} onChange={(e) => setForm({ ...form, endpointUrl: e.target.value })} placeholder="https://peer.example:port" />
          </Field>
          <Field label={t('federation.approvedIps')}>
            <Input value={form.endpointIps} onChange={(e) => setForm({ ...form, endpointIps: e.target.value })} placeholder="203.0.113.10" />
          </Field>
        </div>
        <Field label={t('federation.intendedCertificate')}>
          <textarea
            className="min-h-20 w-full rounded-[var(--radius-md)] border bg-[var(--color-surface-elevated)] p-2 font-mono text-xs text-[var(--color-foreground)]"
            value={form.certificatePem}
            onChange={(e) => setForm({ ...form, certificatePem: e.target.value })}
            placeholder="-----BEGIN CERTIFICATE-----"
          />
        </Field>
        <Field label={t('federation.capabilities')}>
          <Input value={form.capabilities} onChange={(e) => setForm({ ...form, capabilities: e.target.value })} />
        </Field>
        <div className="flex justify-end">
          <Button disabled={disabled || federation.busy} onClick={() => void submit()}>
            {t('federation.createInvitation')}
          </Button>
        </div>
        {bundle && (
          <div className="space-y-2">
            <p className="text-sm font-semibold">{t('federation.bundleCopyNow')}</p>
            <pre className="max-h-64 overflow-auto rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 font-mono text-xs">{bundle}</pre>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function InviteAcceptor({ federation }: { federation: Federation }) {
  const { t } = useLocale()
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
        <CardTitle>{t('federation.acceptInvitation')}</CardTitle>
        <CardDescription>
          {t('federation.acceptInvitationDescription')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Field label={t('federation.bundleJson')}>
          <textarea
            className="min-h-24 w-full rounded-[var(--radius-md)] border bg-[var(--color-surface-elevated)] p-2 font-mono text-xs text-[var(--color-foreground)]"
            value={form.bundleJson}
            onChange={(e) => setForm({ ...form, bundleJson: e.target.value })}
            placeholder='{"protocol_version":1,"invite_id":…}'
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('federation.issuerEndpointUrl')}>
            <Input value={form.endpointUrl} onChange={(e) => setForm({ ...form, endpointUrl: e.target.value })} />
          </Field>
          <Field label={t('federation.approvedIps')}>
            <Input value={form.endpointIps} onChange={(e) => setForm({ ...form, endpointIps: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center justify-between">
          {accepted && <p className="flex items-center gap-1 text-sm text-[var(--color-success)]"><Check className="size-4" aria-hidden="true" /> {t('federation.acceptedGrants')}</p>}
          <Button className="ml-auto" variant="outline" disabled={disabled || federation.busy} onClick={() => void submit()}>
            {t('federation.acceptInvitation')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function ChannelSelector({ federation }: { federation: Federation }) {
  const { t } = useLocale()
  const { channelRef, setChannelRef, bindings } = federation
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
      localRoomId: form.localRoomId.trim() || undefined,
      senderNodeId: form.senderNodeId.trim() || undefined,
      grantEpoch: form.grantEpoch ? Number(form.grantEpoch) : undefined,
    })
  }

  function selectBinding(binding: BindingView) {
    setForm((current) => ({
      ...current,
      authority: binding.authority_node_id,
      channel: binding.channel_id,
      localRoomId: binding.local_room_id,
    }))
    setChannelRef({
      authority: binding.authority_node_id,
      channel: binding.channel_id,
      localRoomId: binding.local_room_id,
      senderNodeId: channelRef?.senderNodeId,
      grantEpoch: channelRef?.grantEpoch,
    })
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle>{t('federation.selectChannel')}</CardTitle>
            <CardDescription>
              {bindings.length > 0
                ? t('federation.selectChannelWithBindings')
                : t('federation.selectChannelManual')}
            </CardDescription>
          </div>
          <Button size="sm" variant="outline" disabled={federation.busy} onClick={() => void federation.sync()}>
            <RefreshCw className="mr-1 size-3" aria-hidden="true" /> {t('federation.sync')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('federation.authorityNodeId')}>
            <Input value={form.authority} onChange={(e) => setForm({ ...form, authority: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.channelId')}>
            <Input value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.thisNodeId')}>
            <Input value={form.senderNodeId} onChange={(e) => setForm({ ...form, senderNodeId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.grantEpoch')}>
            <Input value={form.grantEpoch} onChange={(e) => setForm({ ...form, grantEpoch: e.target.value })} placeholder="1" inputMode="numeric" />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
          <Field label={t('federation.bindLocalRoom')}>
            <Input value={form.localRoomId} onChange={(e) => setForm({ ...form, localRoomId: e.target.value })} placeholder={t('federation.localRoomOptional')} />
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
            {t('federation.bind')}
          </Button>
        </div>
        {bindings.length > 0 && (
          <div className="space-y-2">
            <p className="text-sm font-medium">{t('federation.bindingsOnNode')}</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {bindings.map((binding) => {
                const active =
                  binding.authority_node_id === federation.channelRef?.authority &&
                  binding.channel_id === federation.channelRef?.channel
                return (
                  <button
                    key={`${binding.authority_node_id}:${binding.channel_id}`}
                    type="button"
                    onClick={() => selectBinding(binding)}
                    className={`flex items-center justify-between gap-2 rounded-[var(--radius-md)] border p-3 text-left text-sm transition-colors ${
                      active ? 'border-[var(--color-brand)] bg-[var(--color-brand-tint-bg)]' : 'hover:bg-[var(--color-surface-hover)]'
                    }`}
                  >
                    <span className="min-w-0 truncate font-mono text-xs">
                      {binding.authority_node_id.slice(0, 8)}… / {binding.channel_id.slice(0, 8)}…
                    </span>
                    <span className="shrink-0 text-xs text-[var(--color-foreground-muted)]">
                      {t('federation.sequence', { sequence: binding.applied_seq })}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-[var(--color-foreground-muted)]">
            {federation.channelRef
              ? t('federation.selectedChannel', {
                authority: `${federation.channelRef.authority.slice(0, 8)}…`,
                channel: `${federation.channelRef.channel.slice(0, 8)}…`,
              })
              : t('federation.noChannel')}
          </p>
          <Button disabled={disabled || federation.busy} onClick={apply}>
            {t('federation.useChannel')}
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
  const { t } = useLocale()
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
        <CardTitle>{t('federation.requestExecution')}</CardTitle>
        <CardDescription>
          {t('federation.requestExecutionDescription')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {!commandFieldsReady && (
          <div role="status" className="rounded-[var(--radius-md)] border bg-[var(--color-surface-alt)] p-3 text-sm">
            {t('federation.commandFieldsRequired')}
          </div>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('federation.executorNodeId')}>
            <Input value={form.executorNodeId} onChange={(e) => setForm({ ...form, executorNodeId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.executorAgentId')}>
            <Input value={form.executorAgentId} onChange={(e) => setForm({ ...form, executorAgentId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.taskId')}>
            <Input value={form.taskId} onChange={(e) => setForm({ ...form, taskId: e.target.value })} placeholder="uuid" />
          </Field>
          <Field label={t('federation.delegationIdAuto')}>
            <Input value={form.delegationId} onChange={(e) => setForm({ ...form, delegationId: e.target.value })} placeholder={t('federation.auto')} />
          </Field>
        </div>
        <Field label={t('federation.sourceMessage')}>
          <select
            className="w-full rounded-[var(--radius-md)] border bg-[var(--color-surface-elevated)] p-2 text-sm text-[var(--color-foreground)]"
            value={form.sourceMessageId}
            onChange={(e) => setForm({ ...form, sourceMessageId: e.target.value })}
          >
            <option value="">{t('federation.selectMessage')}</option>
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
            {t('federation.submitTask')}
          </Button>
        </div>
        <div className="rounded-[var(--radius-md)] border p-3">
          <p className="flex items-center gap-2 text-sm font-medium">
            <Ban className="size-4" aria-hidden="true" /> {t('federation.requestCancellation')}
          </p>
          <div className="mt-2 grid gap-3 sm:grid-cols-[minmax(0,1fr)_110px_auto] sm:items-end">
            <Field label={t('federation.delegationId')}>
              <Input value={form.cancelDelegationId} onChange={(e) => setForm({ ...form, cancelDelegationId: e.target.value })} />
            </Field>
            <Field label={t('federation.expectedRevision')}>
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
              {t('federation.requestStop')}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
