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
import {
  applyParticipantChanged,
  delegationLabels,
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
  const online = reachability === 'online'
  return (
    <span className="inline-flex items-center gap-2 text-sm text-[var(--color-foreground-muted)]">
      <StatusDot online={online} />
      {name} · {online ? 'online' : 'offline'}
    </span>
  )
}

const timeline: Array<{ state: DelegationState; title: string; detail: string }> = [
  { state: 'requested', title: 'Request sent', detail: 'The execution node has not accepted it yet.' },
  { state: 'accepted', title: 'Request accepted', detail: 'Acceptance does not mean work has started.' },
  { state: 'running', title: 'Execution started', detail: 'Builder is using resources on Orchard lab.' },
  { state: 'completed', title: 'Result confirmed', detail: 'Garden studio recorded the final result.' },
]

const order: DelegationState[] = ['requested', 'accepted', 'running', 'completed']

export default function FederationWorkspace({
  initialScenario = initialFederationScenario,
  onIntent,
}: FederationWorkspaceProps) {
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
    emit(intent, 'Mock invite accepted locally. Peer acknowledgement is still pending.')
  }

  function confirmPeer() {
    const intent: FederationIntent = { type: 'confirm_peer_receipt', nodeId: scenario.remoteNode.id }
    setScenario((current) => ({
      ...current,
      remoteNode: { ...current.remoteNode, acknowledgement: 'confirmed', sharedChannels: 1 },
    }))
    emit(intent, 'Mock peer acknowledgement received. The scoped connection is now active.')
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
    emit(intent, 'Mock access revoked. Remote participants are now hidden.')
  }

  function setReachability(node: 'localNode' | 'remoteNode') {
    setScenario((current) => {
      const target = current[node]
      const reachability = target.reachability === 'online' ? 'offline' : 'online'
      emit(
        { type: 'set_mock_reachability', nodeId: target.id, reachability },
        `${target.name} is ${reachability} in this mock.`,
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
          ? 'Mock task request committed. The execution node has not accepted it yet.'
          : 'Mock task request saved locally. The channel owner has not confirmed it.',
      )
      setScenario((value) => ({ ...value, delegationState: ownerOnline ? 'requested' : 'unconfirmed', taskStatus: 'todo' }))
      return
    }
    if (current === 'unconfirmed') {
      emit(
        { type: 'confirm_authority_commit' },
        'Mock request committed by Garden studio.',
      )
      setScenario((value) => ({ ...value, delegationState: 'requested', taskStatus: 'todo' }))
      return
    }
    if (current === 'requested') {
      emit(
        { type: 'accept_delegation', agentId: 'builder', nodeId: scenario.remoteNode.id },
        'Mock request accepted. It is still not running.',
      )
      setScenario((value) => ({ ...value, delegationState: 'accepted', processState: 'not_started', taskStatus: 'in_progress' }))
      return
    }
    if (current === 'accepted') {
      setScenario((value) => ({ ...value, delegationState: 'running', processState: 'running', taskStatus: 'in_progress' }))
      setAnnouncement('Mock execution started on Orchard lab.')
      return
    }
    if (current === 'running') {
      setScenario((value) => ({ ...value, delegationState: 'completed', processState: 'finished', taskStatus: 'done' }))
      setAnnouncement('Mock result confirmed by Garden studio.')
    }
  }

  function requestCancel() {
    setScenario((value) => ({ ...value, delegationState: 'cancel_requested', taskStatus: 'blocked' }))
    emit({ type: 'request_cancel' }, 'Mock stop requested. Execution is not confirmed stopped yet.')
  }

  function rejectDelegation() {
    setScenario((value) => ({ ...value, delegationState: 'rejected', processState: 'not_started', taskStatus: 'todo' }))
    emit(
      { type: 'reject_delegation', agentId: 'builder', nodeId: scenario.remoteNode.id },
      'Mock request declined by Orchard lab. Execution did not start.',
    )
  }

  function markUnknown() {
    setScenario((value) => ({ ...value, delegationState: 'unknown', processState: 'unknown', taskStatus: 'blocked' }))
    emit({ type: 'mark_execution_unknown' }, 'Mock outcome recorded as unknown. Automatic retry is blocked.')
  }

  function confirmStop() {
    setScenario((value) => ({ ...value, delegationState: 'cancelled', processState: 'stopped', taskStatus: 'failed' }))
    emit({ type: 'confirm_stop' }, 'Mock execution stopped and cancellation confirmed.')
  }

  function reportKnownFailure() {
    setScenario((value) => ({ ...value, delegationState: 'failed', processState: 'finished', taskStatus: 'failed' }))
    emit(
      { type: 'report_known_failure', errorCode: 'ENGINE_ERROR' },
      'Mock engine failure recorded after confirmed process termination.',
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
    setAnnouncement('Mock participant removal applied. Authorization grants were not changed by this roster event.')
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
    setAnnouncement('Stale participant event ignored. The removal tombstone still wins.')
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
          <span className="font-semibold">Interactive mock · no server connection</span>
          <span className="ml-2">Actions only change this preview and never invite, revoke, or run anything.</span>
        </div>
      </div>

      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-caption text-[var(--color-foreground-muted)]">Federated collaboration preview</p>
          <h1 className="mt-1 text-title">Work together across trusted nodes</h1>
          <p className="mt-2 max-w-2xl text-sm text-[var(--color-foreground-muted)]">
            Connect one node, choose exactly what to share, and keep task ownership separate from execution.
          </p>
        </div>
        <div className="flex flex-wrap gap-2" aria-label="Mock node controls">
          <Button size="sm" variant="outline" onClick={() => setReachability('localNode')}>
            Toggle owner node
          </Button>
          <Button size="sm" variant="outline" onClick={() => setReachability('remoteNode')}>
            Toggle execution node
          </Button>
        </div>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        <Card className={!ownerOnline ? 'border-[var(--color-warning)]' : ''}>
          <CardContent className="flex items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <p className="text-badge text-[var(--color-foreground-subtle)]">CHANNEL OWNER</p>
              <NodeStatus name={scenario.localNode.name} reachability={scenario.localNode.reachability} />
            </div>
            <ShieldCheck className="size-5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          </CardContent>
        </Card>
        <Card className={!executorOnline ? 'border-[var(--color-warning)]' : ''}>
          <CardContent className="flex items-center justify-between gap-4 p-4">
            <div className="min-w-0">
              <p className="text-badge text-[var(--color-foreground-subtle)]">EXECUTION NODE</p>
              <NodeStatus name={scenario.remoteNode.name} reachability={scenario.remoteNode.reachability} />
            </div>
            <Server className="size-5 shrink-0 text-[var(--color-foreground-muted)]" aria-hidden="true" />
          </CardContent>
        </Card>
      </div>

      {!ownerOnline && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div><strong>Channel owner is offline.</strong> Messages and requests can be drafted, but shared changes remain unconfirmed. Local work can continue.</div>
        </div>
      )}
      {ownerOnline && !executorOnline && (
        <div role="alert" className="flex gap-3 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
          <Unplug className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div><strong>Execution node is offline.</strong> The channel remains available, but Builder cannot accept or run this request. It will not be retried automatically.</div>
        </div>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid h-auto w-full grid-cols-3 sm:w-fit">
          <TabsTrigger value="nodes">1. Connection</TabsTrigger>
          <TabsTrigger value="channel">2. Shared channel</TabsTrigger>
          <TabsTrigger value="task">3. Task handoff</TabsTrigger>
        </TabsList>

        <TabsContent value="nodes" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,.8fr)]">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <CardTitle>Connection invitation</CardTitle>
                    <CardDescription>Review the node and its proposed sharing boundary before accepting.</CardDescription>
                  </div>
                  <Badge variant={scenario.remoteNode.connection === 'accepted' ? 'default' : scenario.remoteNode.connection === 'revoked' ? 'destructive' : 'outline'}>
                    {remoteConnected ? 'Connected' : scenario.remoteNode.connection === 'accepted' ? 'Accepted locally · waiting for peer' : scenario.remoteNode.connection === 'revoked' ? 'Access revoked' : 'Needs your review'}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="flex items-center gap-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-4">
                  <div className="flex size-10 items-center justify-center rounded-[var(--radius-md)] bg-white shadow-whisper"><Server className="size-5" aria-hidden="true" /></div>
                  <div className="min-w-0">
                    <p className="font-semibold">{scenario.remoteNode.name}</p>
                    <p className="truncate text-sm text-[var(--color-foreground-muted)]">Node ID · {scenario.remoteNode.id}</p>
                  </div>
                </div>
                <div className="grid gap-3 text-sm sm:grid-cols-2">
                  <div className="rounded-[var(--radius-md)] border p-3"><strong className="block">Requested scope</strong><span className="text-[var(--color-foreground-muted)]">#launch-room and selected agents only</span></div>
                  <div className="rounded-[var(--radius-md)] border p-3"><strong className="block">Not shared</strong><span className="text-[var(--color-foreground-muted)]">Other channels, files, agents, and node settings</span></div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  {scenario.remoteNode.connection === 'pending' && <Button onClick={acceptInvite}>Preview accept</Button>}
                  {scenario.remoteNode.connection === 'accepted' && scenario.remoteNode.acknowledgement === 'waiting' && <Button onClick={confirmPeer}>Preview peer confirmation</Button>}
                  {scenario.remoteNode.connection === 'accepted' && <Button variant="destructive" onClick={revokeAccess}>Preview revoke access</Button>}
                  {scenario.remoteNode.connection === 'revoked' && <Button variant="outline" disabled>Invitation required again</Button>}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className="text-lead">What acceptance means</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-4 text-sm">
                  {[
                    ['Shared channel only', 'No other rooms become visible.'],
                    ['Two-part permission', 'The owner confirms shared state; each node controls its own execution.'],
                    ['Revocable access', 'Revocation hides remote participants and blocks new requests.'],
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
                <div><CardTitle>#launch-room</CardTitle><CardDescription>Owned by {scenario.localNode.name} · {remoteConnected ? '2 trusted nodes' : 'local node only'}</CardDescription></div>
                <Badge variant="outline"><Link2 className="mr-1 size-3" aria-hidden="true" /> Shared channel</Badge>
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
                        <Badge variant={agent.origin === 'local' ? 'secondary' : 'default'}>{agent.origin === 'local' ? 'Local' : `Remote · ${agent.participant.role}`}</Badge>
                        <span className="text-badge text-[var(--color-foreground-subtle)]">{agent.executionAllowed ? 'Execution allowed' : 'No execution access'}</span>
                      </div>
                    </div>
                  ))}
              </div>
              {remoteConnected && remoteBuilder && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-3 text-sm">
                  <p className="text-[var(--color-foreground-muted)]">
                    Participant revision {remoteBuilder.participant.revision} · {remoteBuilder.participant.active ? 'active roster entry' : 'inactive tombstone retained'}
                  </p>
                  {remoteBuilder.participant.active
                    ? <Button size="sm" variant="outline" onClick={removeRemoteParticipant}>Preview participant removal</Button>
                    : <Button size="sm" variant="outline" onClick={replayStaleParticipantAdd}>Replay stale add event</Button>}
                </div>
              )}
              <p className="mt-4 flex items-start gap-2 text-sm text-[var(--color-foreground-muted)]"><ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" />Roster visibility is projected from participant events. Channel grants and execution permission are checked separately and cannot be created or revived by a display event.</p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
            <Card>
              <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle>Prepare release notes</CardTitle><CardDescription>{scenario.delegationState === 'draft' ? 'Prepared by local Planner for remote Builder' : 'Requested from local Planner → remote Builder'}</CardDescription></div><Badge variant={scenario.delegationState === 'completed' ? 'default' : scenario.delegationState === 'cancelled' || scenario.delegationState === 'rejected' || scenario.delegationState === 'failed' ? 'destructive' : 'outline'}>{delegationLabels[scenario.delegationState]}</Badge></div>
              </CardHeader>
              <CardContent>
                <ol className="space-y-1">
                  {timeline.map((item, index) => {
                    const complete = currentIndex >= index
                    const active = scenario.delegationState === item.state
                    return (
                      <li key={item.state} className="grid grid-cols-[28px_1fr] gap-3">
                        <div className="flex flex-col items-center"><span className={`mt-1 flex size-6 items-center justify-center rounded-full border ${complete ? 'border-[var(--color-brand)] bg-[var(--color-brand)] text-white' : 'bg-white text-[var(--color-foreground-subtle)]'}`}>{complete ? <Check className="size-3.5" aria-hidden="true" /> : index + 1}</span>{index < timeline.length - 1 && <span className="min-h-8 w-px flex-1 bg-[var(--color-border)]" />}</div>
                        <div className="pb-5"><p className={active ? 'font-semibold text-[var(--color-foreground)]' : 'font-medium'}>{item.title}</p><p className="text-sm text-[var(--color-foreground-muted)]">{item.detail}</p></div>
                      </li>
                    )
                  })}
                </ol>
                {(scenario.delegationState === 'cancel_requested' || scenario.delegationState === 'cancelled') && (
                  <div role="status" className="mt-2 rounded-[var(--radius-md)] border bg-[var(--color-surface-alt)] p-4 text-sm">
                    <strong>{scenario.delegationState === 'cancel_requested' ? 'Stop is not confirmed yet.' : 'Execution is confirmed stopped.'}</strong>
                    <p className="mt-1 text-[var(--color-foreground-muted)]">A late result remains visible as an observation, but cannot become the completed task after cancellation wins.</p>
                  </div>
                )}
                {scenario.delegationState === 'unknown' && (
                  <div role="alert" className="mt-2 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
                    <strong>Execution may have produced side effects.</strong>
                    <p className="mt-1">Check Orchard lab before deciding what to do next. This task will not retry automatically.</p>
                  </div>
                )}
                {scenario.delegationState === 'unconfirmed' && (
                  <div role="status" className="mt-2 rounded-[var(--radius-md)] border border-orange-300 bg-orange-50 p-4 text-sm text-orange-950">
                    <strong>This request exists only on your node.</strong>
                    <p className="mt-1">It is not reserved, accepted, or visible as shared work until Garden studio confirms it.</p>
                  </div>
                )}
                {scenario.delegationState === 'failed' && (
                  <div role="alert" className="mt-2 rounded-[var(--radius-md)] border border-[var(--color-danger)]/30 bg-red-50 p-4 text-sm text-red-950">
                    <strong>Execution ended with a known failure.</strong>
                    <p className="mt-1">ENGINE_ERROR · the process is confirmed finished. This is not an unknown outcome.</p>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-lead">Available action</CardTitle><CardDescription>Actions reflect both node states.</CardDescription></CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-[var(--radius-md)] bg-[var(--color-surface-alt)] p-4 text-sm">
                  <p className="font-medium">{delegationLabels[scenario.delegationState]}</p>
                  <p className="mt-1 text-[var(--color-foreground-muted)]">Task · {scenario.taskStatus.replace('_', ' ')} · process · {scenario.processState.replace('_', ' ')}. {!remoteConnected ? 'Accept the node invitation before sending this request.' : !remoteBuilder?.participant.active ? 'Builder is no longer an active participant.' : !remoteBuilder.executionAllowed ? 'Builder is visible but has no execution permission.' : !ownerOnline ? 'Wait for the owner node before confirming shared state.' : !executorOnline ? 'Wait for the execution node. Do not automatically retry.' : 'Both nodes are reachable in this mock.'}</p>
                </div>
                {scenario.delegationState === 'draft' && <Button className="w-full" disabled={!executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>Preview task request <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'unconfirmed' && <Button className="w-full" disabled={!ownerOnline} onClick={advanceDelegation}>Preview authority confirmation <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'requested' && <><Button className="w-full" disabled={!ownerOnline || !executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>Preview remote acceptance <ArrowRight aria-hidden="true" /></Button><Button className="w-full" variant="outline" disabled={!executorOnline || !remoteBuilder?.participant.active} onClick={rejectDelegation}>Preview remote rejection</Button></>}
                {scenario.delegationState === 'accepted' && <Button className="w-full" disabled={!executorOnline || !remoteAgentCanRun} onClick={advanceDelegation}>Preview execution start <ArrowRight aria-hidden="true" /></Button>}
                {scenario.delegationState === 'running' && <><Button className="w-full" disabled={!ownerOnline || !executorOnline} onClick={advanceDelegation}>Preview result confirmation</Button><Button className="w-full" variant="outline" disabled={!executorOnline} onClick={reportKnownFailure}>Preview known failure</Button><Button className="w-full" variant="outline" onClick={requestCancel}>Preview stop request</Button></>}
                {(scenario.delegationState === 'accepted' || scenario.delegationState === 'running') && !executorOnline && <Button className="w-full" variant="outline" onClick={markUnknown}>Preview outcome unknown</Button>}
                {scenario.delegationState === 'cancel_requested' && <Button className="w-full" variant="destructive" disabled={!executorOnline} onClick={confirmStop}>Preview stop confirmation</Button>}
                {(scenario.delegationState === 'completed' || scenario.delegationState === 'failed' || scenario.delegationState === 'cancelled' || scenario.delegationState === 'rejected' || scenario.delegationState === 'unknown') && <Button className="w-full" variant="secondary" disabled>{scenario.delegationState === 'completed' ? 'Task is complete' : scenario.delegationState === 'failed' ? 'Task failed' : scenario.delegationState === 'rejected' ? 'Request was declined' : scenario.delegationState === 'unknown' ? 'Automatic retry blocked' : 'Task is cancelled'}</Button>}
                <div className="flex items-start gap-2 text-xs text-[var(--color-foreground-muted)]"><Radio className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />This preview emits adapter intents but has no REST or WebSocket implementation.</div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <p className="sr-only" aria-live="polite">{announcement}</p>
      <footer className="flex items-center gap-2 border-t pt-4 text-xs text-[var(--color-foreground-subtle)]"><MapPin className="size-3.5" aria-hidden="true" />Development preview for GitHub #593 · fixture data only</footer>
    </div>
  )
}
