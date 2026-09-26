import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import AgentRoomsDialog from '@/components/AgentRoomsDialog'
import type { GraphNode, NodeKind } from './types'
import { TEXT_MUTED, TEXT_PRIMARY, TEXT_SUBTLE } from './constants'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

const kindKeys: Record<NodeKind, MessageKey> = {
  user: 'topology.kindUser', machine: 'topology.kindMachine',
  agent: 'topology.kindAgent', room: 'topology.kindRoom', project: 'topology.kindProject',
}

interface Props {
  selected: GraphNode | null
  onClose: () => void
  isAdmin: boolean
}

/**
 * Right-side slide-in detail panel. Dispatches on ``selected.kind``
 * so each node type gets its own field layout. All actions reuse
 * existing navigation / dialog components (AgentRoomsDialog,
 * ``/admin/machines``, ``/rooms/<id>``).
 */
export default function DetailPanel({ selected, onClose, isAdmin }: Props) {
  const { t } = useLocale()
  if (!selected) return null

  return (
    <aside
      className="topology-detail-panel"
      style={{
        width: 320,
        flex: '0 0 320px',
        borderLeft: '1px solid var(--color-border)',
        background: 'var(--color-surface)',
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        overflowY: 'auto',
        animation: 'topology-slide-in 180ms ease-out',
      }}
      aria-label={t('topology.detail', { kind: t(kindKeys[selected.kind]) })}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: 0.125,
              textTransform: 'uppercase',
              color: TEXT_MUTED,
            }}
          >
            {t(kindKeys[selected.kind])}
          </div>
          <div
            style={{
              fontSize: 17,
              fontWeight: 700,
              letterSpacing: '-0.2px',
              color: TEXT_PRIMARY,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={selected.label}
          >
            {selected.label}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={t('topology.closeDetail')}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>
      <DetailBody selected={selected} isAdmin={isAdmin} />
    </aside>
  )
}

function DetailBody({ selected, isAdmin }: { selected: GraphNode; isAdmin: boolean }) {
  switch (selected.kind as NodeKind) {
    case 'machine':
      return <MachineDetail selected={selected} isAdmin={isAdmin} />
    case 'agent':
      return <AgentDetail selected={selected} />
    case 'room':
      return <RoomDetail selected={selected} />
    case 'user':
      return <UserDetail selected={selected} />
    case 'project':
      return <ProjectDetail selected={selected} />
    default:
      return null
  }
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === null || value === undefined || value === '') {
    return null
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span
        style={{
          fontSize: 11,
          fontWeight: 500,
          color: TEXT_SUBTLE,
          letterSpacing: 0.125,
          textTransform: 'uppercase',
        }}
      >
        {label}
      </span>
      <span style={{ fontSize: 13, color: TEXT_PRIMARY, wordBreak: 'break-all' }}>
        {value}
      </span>
    </div>
  )
}

function MachineDetail({ selected, isAdmin }: { selected: GraphNode; isAdmin: boolean }) {
  const { t } = useLocale()
  const d = selected.data as Record<string, unknown>
  const navigate = useNavigate()
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Field label={t('topology.status')} value={<Badge variant="secondary">{String(d.status ?? t('common.unknown'))}</Badge>} />
      <Field label={t('topology.hostname')} value={d.hostname as string} />
      <Field label={t('topology.daemonVersion')} value={d.daemon_version as string} />
      <Field label={t('topology.agents')} value={d.agent_count as number} />
      {isAdmin && (
        <Button
          variant="outline"
          onClick={() => navigate('/admin/machines')}
        >
          <ExternalLink className="h-4 w-4" />
          {t('topology.manageMachines')}
        </Button>
      )}
    </div>
  )
}

function AgentDetail({ selected }: { selected: GraphNode }) {
  const { t } = useLocale()
  const d = selected.data as Record<string, unknown>
  const [roomsOpen, setRoomsOpen] = useState(false)
  const rawId = selected.id.startsWith('a_') ? selected.id.slice(2) : selected.id
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Field label={t('topology.engine')} value={d.engine as string} />
      <Field label={t('topology.model')} value={d.model as string} />
      <Field
        label={t('topology.state')}
        value={<Badge variant="secondary">{String(d.actual_state ?? t('common.unknown'))}</Badge>}
      />
      <Field label={t('topology.desired')} value={d.desired_state as string} />
      <Field label={t('topology.lastHeartbeat')} value={d.last_heartbeat_at as string} />
      <Field label={t('topology.lastCrash')} value={d.last_crash_reason as string} />
      <Button variant="outline" onClick={() => setRoomsOpen(true)}>
        {t('topology.viewRooms')}
      </Button>
      <AgentRoomsDialog
        open={roomsOpen}
        onOpenChange={setRoomsOpen}
        agentId={rawId}
      />
    </div>
  )
}

function RoomDetail({ selected }: { selected: GraphNode }) {
  const { t } = useLocale()
  const d = selected.data as Record<string, unknown>
  const navigate = useNavigate()
  const rawId = selected.id.startsWith('r_') ? selected.id.slice(2) : selected.id
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Field label={t('topology.type')} value={d.is_dm ? t('topology.directMessage') : t('topology.channel')} />
      <Field label={t('topology.participants')} value={d.participant_count as number} />
      {d.parent_room_id ? <Field label={t('topology.parentRoom')} value={String(d.parent_room_id)} /> : null}
      {d.representative_agent_id ? (
        <Field label={t('topology.representativeAgent')} value={String(d.representative_agent_id)} />
      ) : null}
      <Button variant="default" onClick={() => navigate(`/rooms/${rawId}`)}>
        <ExternalLink className="h-4 w-4" />
        {t('topology.openChat')}
      </Button>
    </div>
  )
}

function UserDetail({ selected }: { selected: GraphNode }) {
  const { t } = useLocale()
  const d = selected.data as Record<string, unknown>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Field label={t('topology.emailLabel')} value={selected.label} />
      <Field label={t('topology.displayName')} value={d.display_name as string} />
      <Field label={t('topology.admin')} value={d.is_admin ? t('topology.yes') : t('topology.no')} />
    </div>
  )
}

function ProjectDetail({ selected }: { selected: GraphNode }) {
  const { t } = useLocale()
  const d = selected.data as Record<string, unknown>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Field label={t('topology.name')} value={selected.label} />
      <Field label={t('topology.description')} value={d.description as string} />
    </div>
  )
}
