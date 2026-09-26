import { useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleDashed,
  Link2,
  MapPin,
  Radio,
  Server,
  ShieldCheck,
  Unplug,
  UserRound,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'
import {
  applyParticipantChanged,
  initialFederationScenario,
  type DelegationState,
  type FederationIntent,
  type FederationScenario,
  type NodeReachability,
} from '@/lib/federationMock'

interface FederationWorkspaceProps {
  initialScenario?: FederationScenario
  onIntent?: (intent: FederationIntent) => void
}

function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      aria-hidden="true"
      className="size-2 shrink-0 rounded-full"
      style={{ background: online ? 'var(--color-status-online)' : 'var(--color-foreground-subtle)' }}
    />
  )
}

function NodeStatus({ name, reachability }: { name: string; reachability: NodeReachability }) {
  const { t } = useLocale()
  const online = reachability === 'online'
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--color-foreground-muted)]">
      <StatusDot online={online} />
      {t('federation.mock.nodeStatus', { name, status: t(online ? 'common.online' : 'common.offline') })}
    </span>
  )
}

const timeline: Array<{ state: DelegationState; title: MessageKey; detail: MessageKey }> = [
  { state: 'requested', title: 'federation.mock.timelineRequested', detail: 'federation.mock.timelineRequestedBody' },
  { state: 'accepted', title: 'federation.mock.timelineAccepted', detail: 'federation.mock.timelineAcceptedBody' },
  { state: 'running', title: 'federation.mock.timelineRunning', detail: 'federation.mock.timelineRunningBody' },
  { state: 'completed', title: 'federation.mock.timelineCompleted', detail: 'federation.mock.timelineCompletedBody' },
]

const order: DelegationState[] = ['requested', 'accepted', 'running', 'completed']

const delegationStateKeys: Record<DelegationState, MessageKey> = {
  draft: 'federation.mock.stateDraft',
  unconfirmed: 'federation.mock.stateUnconfirmed',
  requested: 'federation.mock.stateRequested',
  accepted: 'federation.mock.stateAccepted',
  running: 'federation.mock.stateRunning',
  completed: 'federation.mock.stateCompleted',
  failed: 'federation.mock.stateFailed',
  rejected: 'federation.mock.stateRejected',
  cancel_requested: 'federation.mock.stateCancelRequested',
  cancelled: 'federation.mock.stateCancelled',
  unknown: 'federation.mock.stateUnknown',
}

export default function FederationWorkspace({
  initialScenario = initialFederationScenario,
  onIntent,
}: FederationWorkspaceProps) {
  const { t } = useLocale()
  const [scenario, setScenario] = useState(initialScenario)
  const [tab, setTab] = useState('nodes')
  const [announcement, setAnnouncement] = useState('')

  const visibleAgents = useMemo(
    () => scenario.agents.filter((agent) => agent.published && agent.participant.active),
    [scenario.agents],
  )
  const remoteBuilder = scenario.agents.find(
    (agent) => agent.nodeId === scenario.remoteNode.id && agent.id === 'builder',
  )
  const remoteConnected = scenario.remoteNode.connection === 'accepted'
    && scenario.remoteNode.acknowledgement === 'confirmed'
  const ownerOnline = scenario.localNode.reachability === 'online'
  const executorOnline = scenario.remoteNode.reachability === 'online'
  const remoteAgentCanRun = remoteConnected
    && remoteBuilder?.participant.active === true
    && remoteBuilder.executionAllowed

  function emit(intent: FederationIntent, message: string) {
    onIntent?.(intent)
    setAnnouncement(message)
  }

  function acceptInvite() {
    const intent: FederationIntent = { type: 'accept_node_invite', nodeId: scenario.remoteNode.id }
    setScenario((current) => ({
      ...current,
      remoteNode: { ...current.remoteNode, connection: 'accepted', acknowledgement: 'waiting', sharedChannels: 0 },
    }))
    emit(intent, t('federation.mock.announceInvite'))
  }

  function confirmPeer() {
    const intent: FederationIntent = { type: 'confirm_peer_receipt', nodeId: scenario.remoteNode.id }
    setScenario((current) => ({
      ...current,
      remoteNode: { ...current.remoteNode, acknowledgement: 'confirmed', sharedChannels: 1 },
    }))
    emit(intent, t('federation.mock.announcePeer'))
  }

  function revokeAccess() {
    const intent: FederationIntent = { type: 'revoke_node_access', nodeId: scenario.remoteNode.id }
    setScenario((current) => ({
      ...current,
      remoteNode: { ...current.remoteNode, connection: 'revoked', sharedChannels: 0 },
      delegationState: 'cancelled',
      processState: 'stopped',
      taskStatus: 'failed',
    }))
    emit(intent, t('federation.mock.announceRevoke'))
  }

  function setReachability(node: 'localNode' | 'remoteNode') {
    setScenario((current) => {
      const target = current[node]
      const reachability = target.reachability === 'online' ? 'offline' : 'online'
      emit(
        { type: 'set_mock_reachability', nodeId: target.id, reachability },
        t('federation.mock.announceReachability', {
          name: target.name,
          status: t(reachability === 'online' ? 'common.online' : 'common.offline'),
        }),
      )
      return { ...current, [node]: { ...target, reachability } }
    })
  }

  function advanceDelegation() {
    const current = scenario.delegationState
    if (current === 'draft') {
      emit(
        { type: 'request_delegation', agentId: 'builder', nodeId: scenario.remoteNode.id },
        ownerOnline
          ? t('federation.mock.announceRequestCommitted')
          : t('federation.mock.announceRequestSaved'),
      )
      setScenario((value) => ({ ...value, delegationState: ownerOnline ? 'requested' : 'unconfirmed', taskStatus: 'todo' }))
      return
    }
    if (current === 'unconfirmed') {
      emit(
        { type: 'confirm_authority_commit' },
        t('federation.mock.announceOwnerCommit'),
      )
      setScenario((value) => ({ ...value, delegationState: 'requested', taskStatus: 'todo' }))
      return
    }
    if (current === 'requested') {
      emit(
        { type: 'accept_delegation', agentId: 'builder', nodeId: scenario.remoteNode.id },
        t('federation.mock.announceAccepted'),
      )
      setScenario((value) => ({ ...value, delegationState: 'accepted', processState: 'not_started', taskStatus: 'in_progress' }))
      return
    }
    if (current === 'accepted') {
      setScenario((value) => ({ ...value, delegationState: 'running', processState: 'running', taskStatus: 'in_progress' }))
      setAnnouncement(t('federation.mock.announceStarted'))
      return
    }
    if (current === 'running') {
      setScenario((value) => ({ ...value, delegationState: 'completed', processState: 'finished', taskStatus: 'done' }))
      setAnnouncement(t('federation.mock.announceResult'))
    }
  }

  function requestCancel() {
    setScenario((value) => ({ ...value, delegationState: 'cancel_requested', taskStatus: 'blocked' }))
    emit({ type: 'request_cancel' }, t('federation.mock.announceStopRequest'))
  }

  function rejectDelegation() {
    setScenario((value) => ({ ...value, delegationState: 'rejected', processState: 'not_started', taskStatus: 'todo' }))
    emit(
      { type: 'reject_delegation', agentId: 'builder', nodeId: scenario.remoteNode.id },
      t('federation.mock.announceRejected'),
    )
  }

  function markUnknown() {
    setScenario((value) => ({ ...value, delegationState: 'unknown', processState: 'unknown', taskStatus: 'blocked' }))
    emit({ type: 'mark_execution_unknown' }, t('federation.mock.announceUnknown'))
  }

  function confirmStop() {
    setScenario((value) => ({ ...value, delegationState: 'cancelled', processState: 'stopped', taskStatus: 'failed' }))
    emit({ type: 'confirm_stop' }, t('federation.mock.announceStopped'))
  }

  function reportKnownFailure() {
    setScenario((value) => ({ ...value, delegationState: 'failed', processState: 'finished', taskStatus: 'failed' }))
    emit(
      { type: 'report_known_failure', errorCode: 'ENGINE_ERROR' },
      t('federation.mock.announceFailure'),
    )
  }

  function removeRemoteParticipant() {
    if (!remoteBuilder) return
    setScenario((current) => applyParticipantChanged(current, {
      seq: 42,
      kind: 'participant.changed',
      principal: { nodeId: remoteBuilder.nodeId, agentId: remoteBuilder.id },
      active: false,
      role: remoteBuilder.participant.role,
      revision: remoteBuilder.participant.revision + 1,
    }))
    setAnnouncement(t('federation.mock.announceRemove'))
  }

  function replayStaleParticipantAdd() {
    if (!remoteBuilder) return
    setScenario((current) => applyParticipantChanged(current, {
      seq: 41,
      kind: 'participant.changed',
      principal: { nodeId: remoteBuilder.nodeId, agentId: remoteBuilder.id },
      active: true,
      role: remoteBuilder.participant.role,
      revision: Math.max(1, remoteBuilder.participant.revision - 1),
    }))
    setAnnouncement(t('federation.mock.announceStale'))
  }

  const currentIndex = scenario.delegationState === 'rejected'
    ? 0
    : scenario.delegationState === 'cancel_requested' || scenario.delegationState === 'cancelled' || scenario.delegationState === 'unknown' || scenario.delegationState === 'failed'
      ? 2
      : order.indexOf(scenario.delegationState)

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
      <div
        role="status"
        className="flex items-start gap-3 rounded-[var(--radius-md)] border border-[var(--color-brand)]/20 bg-[var(--color-brand-tint-bg)] px-4 py-3 text-sm text-[var(--color-brand-tint-text)]"
      >
        <CircleDashed className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <div>
          <span className="font-semibold">{t('federation.previewNotice')}</span>
          <span className="ml-2">{t('federation.previewNoticeBody')}</span>
        </div>
      </div>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-caption text-[var(--color-foreground-muted)]">{t('federation.previewEyebrow')}</p>
          <h1 className="mt-1 text-heading">{t('federation.title')}</h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--color-foreground-muted)]">
            {t('federation.description')}
          </p>
        </div>
        <div className="flex flex-wrap gap-2" aria-label={t('federation.mock.controls')}>
          <Button size="sm" variant="outline" onClick={() => setReachability('localNode')}>
            {t('federation.mock.toggleOwner')}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setReachability('remoteNode')}>
            {t('federation.mock.toggleExecutor')}
          </Button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <Card className={!ownerOnline ? 'border-[var(--color-warning)]' : ''}>
          <CardContent className="flex items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <p className="text-badge text-[var(--color-foreground-subtle)]">{t('federation.mock.owner')}</p>
              <NodeStatus name={scenario.localNode.name} reachability={scenario.localNode.reachability} />
            </div>
            <ShieldCheck className="size-5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          </CardContent>
        </Card>
        <Card className={!executorOnline ? 'border-[var(--color-warning)]' : ''}>
          <CardContent className="flex items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <p className="text-badge text-[var(--color-foreground-subtle)]">{t('federation.mock.executor')}</p>
              <NodeStatus name={scenario.remoteNode.name} reachability={scenario.remoteNode.reachability} />
            </div>
            <Server className="size-5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          </CardContent>
        </Card>
      </div>

      {!ownerOnline && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div><strong>{t('federation.mock.ownerOfflineTitle')}</strong> {t('federation.mock.ownerOfflineBody')}</div>
        </div>
      )}
      {ownerOnline && !executorOnline && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
          <Unplug className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div><strong>{t('federation.mock.executorOfflineTitle')}</strong> {t('federation.mock.executorOfflineBody')}</div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid h-auto w-full grid-cols-3 sm:w-fit">
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="nodes">{t('federation.tabConnection')}</TabsTrigger>
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="channel">{t('federation.tabChannel')}</TabsTrigger>
          <TabsTrigger className="min-w-0 whitespace-normal px-1.5 text-center leading-tight sm:whitespace-nowrap sm:px-3" value="task">{t('federation.tabTask')}</TabsTrigger>
        </TabsList>

        <TabsContent value="nodes" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,.8fr)]">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle>{t('federation.mock.connectionInvitation')}</CardTitle>
                    <CardDescription>{t('federation.mock.connectionInvitationDescription')}</CardDescription>
                  </div>
                  <Badge variant={scenario.remoteNode.connection === 'accepted' ? 'default' : scenario.remoteNode.connection === 'revoked' ? 'destructive' : 'outline'}>
                    {remoteConnected ? t('federation.mock.connected') : scenario.remoteNode.connection === 'accepted' ? t('federation.mock.acceptedWaiting') : scenario.remoteNode.connection === 'revoked' ? t('federation.mock.accessRevoked') : t('federation.mock.needsReview')}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-center gap-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-4">
                  <div className="flex size-10 items-center justify-center rounded-[var(--radius-md)] bg-[var(--color-surface-elevated)] shadow-whisper"><Server className="size-5" aria-hidden="true" /></div>
                  <div className="min-w-0">
                    <p className="font-semibold">{scenario.remoteNode.name}</p>
                    <p className="truncate text-sm text-[var(--color-foreground-muted)]">{t('federation.mock.nodeId', { id: scenario.remoteNode.id })}</p>
                  </div>
                </div>
                <div className="grid gap-3 text-sm sm:grid-cols-2">
                  <div className="rounded-[var(--radius-md)] border p-3"><strong className="block">{t('federation.mock.requestedScope')}</strong><span className="text-[var(--color-foreground-muted)]">{t('federation.mock.requestedScopeBody')}</span></div>
                  <div className="rounded-[var(--radius-md)] border p-3"><strong className="block">{t('federation.mock.notShared')}</strong><span className="text-[var(--color-foreground-muted)]">{t('federation.mock.notSharedBody')}</span></div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  {scenario.remoteNode.connection === 'pending' && <Button onClick={acceptInvite}>{t('federation.mock.previewAccept')}</Button>}
                  {scenario.remoteNode.connection === 'accepted' && scenario.remoteNode.acknowledgement === 'waiting' && <Button onClick={confirmPeer}>{t('federation.mock.previewConfirmPeer')}</Button>}
                  {scenario.remoteNode.connection === 'accepted' && <Button variant="destructive" onClick={revokeAccess}>{t('federation.mock.previewRevoke')}</Button>}
                  {scenario.remoteNode.connection === 'revoked' && <Button variant="outline" disabled>{t('federation.mock.invitationAgain')}</Button>}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-lead">{t('federation.mock.acceptanceMeans')}</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-4 text-sm">
                  {[
                    [t('federation.mock.sharedChannelOnly'), t('federation.mock.sharedChannelOnlyBody')],
                    [t('federation.mock.twoPartPermission'), t('federation.mock.twoPartPermissionBody')],
                    [t('federation.mock.revocableAccess'), t('federation.mock.revocableAccessBody')],
                  ].map(([title, detail]) => (
                    <li key={title} className="flex gap-3"><Check className="mt-0.5 size-4 shrink-0 text-[var(--color-success)]" aria-hidden="true" /><span><strong className="block">{title}</strong><span className="text-[var(--color-foreground-muted)]">{detail}</span></span></li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="channel" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><CardTitle>#launch-room</CardTitle><CardDescription>{t('federation.mock.ownedBy', { name: scenario.localNode.name, scope: t(remoteConnected ? 'federation.mock.twoTrustedNodes' : 'federation.mock.localOnly') })}</CardDescription></div>
                <Badge variant="outline"><Link2 className="mr-1 size-3" aria-hidden="true" /> {t('federation.sharedChannel')}</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 sm:grid-cols-2">
                {visibleAgents
                  .filter((agent) => agent.origin === 'local' || remoteConnected)
                  .map((agent) => (
                    <div key={`${agent.nodeId}:${agent.id}`} className="flex items-center justify-between gap-3 rounded-[var(--radius-md)] border p-4">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface-alt)]"><UserRound className="size-4" aria-hidden="true" /></div>
                        <div className="min-w-0"><p className="truncate font-medium">{agent.name}</p><p className="truncate text-xs text-[var(--color-foreground-muted)]">{agent.nodeId} · {agent.id}</p></div>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-1">
                        <Badge variant={agent.origin === 'local' ? 'secondary' : 'default'}>{agent.origin === 'local' ? t('federation.mock.local') : t('federation.mock.remoteRole', { role: agent.participant.role })}</Badge>
                        <span className="text-badge text-[var(--color-foreground-subtle)]">{t(agent.executionAllowed ? 'federation.mock.executionAllowed' : 'federation.mock.noExecution')}</span>
                      </div>
                    </div>
                  ))}
              </div>
              {remoteConnected && remoteBuilder && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 text-sm">
                  <p className="text-[var(--color-foreground-muted)]">
                    {t('federation.mock.participantRevision', {
                      revision: remoteBuilder.participant.revision,
                      status: t(remoteBuilder.participant.active ? 'federation.mock.activeRoster' : 'federation.mock.inactiveTombstone'),
                    })}
                  </p>
                  {remoteBuilder.participant.active
                    ? <Button size="sm" variant="outline" onClick={removeRemoteParticipant}>{t('federation.mock.previewRemoveParticipant')}</Button>
                    : <Button size="sm" variant="outline" onClick={replayStaleParticipantAdd}>{t('federation.mock.replayStale')}</Button>}
                </div>
              )}
              <p className="mt-4 flex items-start gap-2 text-sm text-[var(--color-foreground-muted)]"><ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{t('federation.mock.rosterVisibility')}</p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>{t('federation.mock.prepareNotes')}</CardTitle><CardDescription>{t(scenario.delegationState === 'draft' ? 'federation.mock.preparedBy' : 'federation.mock.requestedFrom')}</CardDescription></div><Badge variant={scenario.delegationState === 'completed' ? 'default' : scenario.delegationState === 'cancelled' || scenario.delegationState === 'rejected' || scenario.delegationState === 'failed' ? 'destructive' : 'outline'}>{t(delegationStateKeys[scenario.delegationState])}</Badge></div>
              </CardHeader>
              <CardContent>
                <ol className="space-y-1">
                  {timeline.map((item, index) => {
                    const complete = currentIndex >= index
                    const active = scenario.delegationState === item.state
                    return (
                      <li key={item.state} className="grid grid-cols-[28px_1fr] gap-3">
                        <div className="flex flex-col items-center"><span className={`mt-1 flex size-6 items-center justify-center rounded-full border ${complete ? 'border-[var(--color-brand)] bg-[var(--color-brand)] text-[var(--color-on-brand)]' : 'bg-[var(--color-surface-elevated)] text-[var(--color-foreground-subtle)]'}`}>{complete ? <Check className="size-3.5" aria-hidden="true" /> : index + 1}</span>{index < timeline.length - 1 && <span className="min-h-8 w-px flex-1 bg-[var(--color-border)]" />}</div>
                        <div className="pb-5"><p className={active ? 'font-semibold text-[var(--color-foreground)]' : 'font-medium'}>{t(item.title)}</p><p className="text-sm text-[var(--color-foreground-muted)]">{t(item.detail)}</p></div>
                      </li>
                    )
                  })}
                </ol>
                {(scenario.delegationState === 'cancel_requested' || scenario.delegationState === 'cancelled') && (
                  <div role="status" className="mt-2 rounded-[var(--radius-md)] border bg-[var(--color-surface-alt)] p-4 text-sm">
                    <strong>{t(scenario.delegationState === 'cancel_requested' ? 'federation.mock.stopNotConfirmed' : 'federation.mock.stopConfirmed')}</strong>
                    <p className="mt-1 text-[var(--color-foreground-muted)]">{t('federation.mock.lateResult')}</p>
                  </div>
                )}
                {scenario.delegationState === 'unknown' && (
                  <div role="alert" className="mt-2 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
                    <strong>{t('federation.mock.sideEffects')}</strong>
                    <p className="mt-1">{t('federation.mock.checkExecutor')}</p>
                  </div>
                )}
                {scenario.delegationState === 'unconfirmed' && (
                  <div role="status" className="mt-2 rounded-[var(--radius-md)] border border-[var(--color-warning)]/30 bg-[var(--color-warning-soft)] p-4 text-sm text-[var(--color-warning)]">
                    <strong>{t('federation.mock.localRequestOnly')}</strong>
                    <p className="mt-1">{t('federation.mock.unconfirmedRequest')}</p>
                  </div>
                )}
                {scenario.delegationState === 'failed' && (
                  <div role="alert" className="mt-2 rounded-[var(--radius-md)] border border-[var(--color-danger)]/30 bg-[var(--color-danger-soft)] p-4 text-sm text-[var(--color-danger)]">
                    <strong>{t('federation.mock.knownFailure')}</strong>
                    <p className="mt-1">{t('federation.mock.knownFailureBody')}</p>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-lead">{t('federation.mock.availableAction')}</CardTitle><CardDescription>{t('federation.mock.actionsDescription')}</CardDescription></CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-4 text-sm">
                  <p className="font-medium">{t(delegationStateKeys[scenario.delegationState])}</p>
                  <p className="mt-1 text-[var(--color-foreground-muted)]">{t('federation.mock.taskProcess', { task: scenario.taskStatus.replace('_', ' '), process: scenario.processState.replace('_', ' ') })} {t(!remoteConnected ? 'federation.mock.needInvitation' : !remoteBuilder?.participant.active ? 'federation.mock.builderInactive' : !remoteBuilder.executionAllowed ? 'federation.mock.builderNoPermission' : !ownerOnline ? 'federation.mock.waitOwner' : !executorOnline ? 'federation.mock.waitExecutor' : 'federation.mock.bothReachable')}</p>
                </div>
                {scenario.delegationState === 'draft' && <Button className="w-full" disabled={!executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>{t('federation.mock.previewTask')} <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'unconfirmed' && <Button className="w-full" disabled={!ownerOnline} onClick={advanceDelegation}>{t('federation.mock.previewAuthority')} <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'requested' && <><Button className="w-full" disabled={!ownerOnline || !executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>{t('federation.mock.previewRemoteAccept')} <ArrowRight aria-hidden="true" /></Button><Button className="w-full" variant="outline" disabled={!executorOnline || !remoteBuilder?.participant.active} onClick={rejectDelegation}>{t('federation.mock.previewRemoteReject')}</Button></>}
                {scenario.delegationState === 'accepted' && <Button className="w-full" disabled={!executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>{t('federation.mock.previewStart')} <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'running' && <><Button className="w-full" disabled={!ownerOnline || !executorOnline} onClick={advanceDelegation}>{t('federation.mock.previewResult')}</Button><Button className="w-full" variant="outline" disabled={!executorOnline} onClick={reportKnownFailure}>{t('federation.mock.previewKnownFailure')}</Button><Button className="w-full" variant="outline" onClick={requestCancel}>{t('federation.mock.previewStopRequest')}</Button></>}
                {(scenario.delegationState === 'accepted' || scenario.delegationState === 'running') && !executorOnline && <Button className="w-full" variant="outline" onClick={markUnknown}>{t('federation.mock.previewUnknown')}</Button>}
                {scenario.delegationState === 'cancel_requested' && <Button className="w-full" variant="destructive" disabled={!executorOnline} onClick={confirmStop}>{t('federation.mock.previewStopConfirm')}</Button>}
                {(scenario.delegationState === 'completed' || scenario.delegationState === 'failed' || scenario.delegationState === 'cancelled' || scenario.delegationState === 'rejected' || scenario.delegationState === 'unknown') && <Button className="w-full" variant="secondary" disabled>{t(scenario.delegationState === 'completed' ? 'federation.mock.taskComplete' : scenario.delegationState === 'failed' ? 'federation.mock.taskFailed' : scenario.delegationState === 'rejected' ? 'federation.mock.requestDeclined' : scenario.delegationState === 'unknown' ? 'federation.mock.retryBlocked' : 'federation.mock.taskCancelled')}</Button>}
                <div className="flex items-start gap-2 text-xs text-[var(--color-foreground-muted)]"><Radio className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />{t('federation.mock.noTransport')}</div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <p className="sr-only" aria-live="polite">{announcement}</p>
      <footer className="flex items-center gap-2 border-t pt-4 text-xs text-[var(--color-foreground-subtle)]"><MapPin className="size-3.5" aria-hidden="true" />{t('federation.mock.footer')}</footer>
    </div>
  )
}
