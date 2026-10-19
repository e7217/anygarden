import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle,
  Check,
  Link2,
  Radio,
  RefreshCw,
  Server,
  ShieldCheck,
  UserRound,
} from 'lucide-react'

import {
  CertificateExpiry,
  ConnectionError,
  InviteAcceptor,
  InviteComposer,
  NodeConnectionCard,
  PeerCertificateRotation,
} from '@/components/FederationConnectionSetup'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { BindingView, ParticipantView } from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'
import { useRooms } from '@/hooks/useRooms'
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
 * #593 live workspace (task #33). Node connections and shared-channel
 * bindings, fed by the real node/shared-channel endpoints via
 * ``useFederation``. Where the backend does not expose a surface yet the UI
 * says so instead of inventing data:
 *
 * - sharing not wired on this node → banner, forms disabled (#34);
 * - delegating, following and stopping work happens in the bound shared
 *   room, not here (#889);
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
    busy,
    actionError,
  } = federation

  // #839 — show a setup error inside the card whose action produced it.
  const [errorOwner, setErrorOwner] = useState<string | null>(null)
  const [rotating, setRotating] = useState<string | null>(null)
  const setupErrorProps = (owner: string) => ({
    error: errorOwner === owner ? actionError : null,
    onAction: () => setErrorOwner(owner),
  })

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
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-foreground-muted)]">
            {t('federation.machinesHintBefore')}
            <Link to="/admin/machines" className="font-medium text-[var(--color-brand-text)] underline underline-offset-2">
              {t('navigation.machines')}
            </Link>
            {t('federation.machinesHintAfter')}
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
            {federation.nodesError?.code === 'CERTIFICATE_EXPIRED' ? (
              <>
                <strong>{t('federation.cert.expiredTitle')}</strong>{' '}
                <span>{t('federation.cert.expiredBody')}</span>
              </>
            ) : (
              <>
                <strong>{t('federation.sharingDisabledTitle')}</strong>{' '}
                <span>{t('federation.sharingDisabledBody')}</span>
              </>
            )}
          </div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid h-auto w-full grid-cols-2 sm:w-fit">
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="nodes">{t('federation.tabConnection')}</TabsTrigger>
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="channel">{t('federation.tabChannel')}</TabsTrigger>
        </TabsList>

        <TabsContent value="nodes" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-2">
            {/* min-w-0: long node IDs must truncate, not widen the grid track. */}
            <Card className="min-w-0">
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
                  <div key={peer.node_id} className="rounded-[var(--radius-md)] border p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="flex items-center gap-2 font-medium">
                        <Server className="size-4" aria-hidden="true" />
                        <span className="truncate font-mono text-sm">{peer.node_id}</span>
                      </p>
                      <p className="mt-1 truncate text-xs text-[var(--color-foreground-muted)]">
                        {t('federation.pin')} {peer.fingerprint.slice(0, 16)}… · {t('federation.epoch')} {peer.certificate_epoch} · {peer.state}
                      </p>
                      {peer.certificate_expires_at && (
                        <p className="mt-1 text-xs text-[var(--color-foreground-muted)]">
                          {t('federation.connect.expires')}{' '}
                          <CertificateExpiry expiresAt={peer.certificate_expires_at} owner="peer" />
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        aria-expanded={rotating === peer.node_id}
                        onClick={() => setRotating(rotating === peer.node_id ? null : peer.node_id)}
                      >
                        {t('federation.cert.rotate')}
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={busy}
                        onClick={() => {
                          setErrorOwner(null)
                          void federation.revokePeer(peer.node_id)
                        }}
                      >
                        {t('federation.revoke')}
                      </Button>
                    </div>
                  </div>
                  {rotating === peer.node_id && (
                    <PeerCertificateRotation
                      federation={federation}
                      nodeId={peer.node_id}
                      epoch={peer.certificate_epoch}
                      onDone={() => setRotating(null)}
                    />
                  )}
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card className="min-w-0">
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
                        onClick={() => {
                          setErrorOwner(null)
                          void federation.revokeInvite(invite.id)
                        }}
                      >
                        {t('federation.revoke')}
                      </Button>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {errorOwner === null && (
            <div className="mt-4">
              <ConnectionError error={actionError} />
            </div>
          )}
          <NodeConnectionCard federation={federation} {...setupErrorProps('card')} />
          <InviteComposer federation={federation} {...setupErrorProps('invite')} />
          <InviteAcceptor federation={federation} {...setupErrorProps('accept')} />
        </TabsContent>

        <TabsContent value="channel" className="mt-4">
          {federation.channelRef?.localRoomId && (
            <Card className="mb-4">
              <CardHeader>
                <CardTitle>{t('federation.room.openConversation')}</CardTitle>
                <CardDescription>{t('federation.room.adminHandoff')}</CardDescription>
              </CardHeader>
              <CardContent>
                <Button asChild><Link to={`/rooms/${federation.channelRef.localRoomId}`}>{t('federation.room.openConversation')}</Link></Button>
              </CardContent>
            </Card>
          )}
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

      </Tabs>
    </div>
  )
}

function ChannelSelector({ federation }: { federation: Federation }) {
  const { t } = useLocale()
  const { channelRef, setChannelRef, bindings } = federation
  const { projects, rooms, refetch } = useRooms()
  // #889 — pick the local room instead of typing its UUID. DMs and rooms
  // already bound to a shared channel cannot take another binding.
  useEffect(() => { void refetch().catch(() => {}) }, [refetch])
  const bindableRooms = useMemo(() => projects.flatMap((project) =>
    (rooms[project.id] ?? [])
      .filter((room) => !room.is_dm && !room.shared_channel)
      .map((room) => ({ id: room.id, label: `${project.name} / ${room.name}` })),
  ), [projects, rooms])
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
            <Select value={form.localRoomId} onChange={(e) => setForm({ ...form, localRoomId: e.target.value })}>
              <option value="">{t('federation.selectLocalRoom')}</option>
              {bindableRooms.map((room) => (
                <option key={room.id} value={room.id}>{room.label}</option>
              ))}
            </Select>
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
