import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import CreateAgentDialog from '@/components/CreateAgentDialog'
import type { Agent, useAgents } from '@/hooks/useAgents'
import { useMachines } from '@/hooks/useMachines'
import { useMachineDetail } from '@/hooks/useMachineDetail'
import { useRooms } from '@/hooks/useRooms'
import { useLocale } from '@/i18n/LocaleProvider'

type AgentsApi = ReturnType<typeof useAgents>

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  createAgent: AgentsApi['createAgent']
  fetchEngineCatalog: AgentsApi['fetchEngineCatalog']
  availableEngines: AgentsApi['availableEngines']
  onCreated: (agent: Agent) => void
}

/**
 * Sidebar entry point for agent creation. The machines page creates an agent
 * on the machine it is showing; here the machine is chosen inside the dialog:
 * a single online machine is picked silently, several get a picker, and none
 * leads the admin to the machines page instead of an unusable form.
 */
export default function SidebarCreateAgentDialog({ open, onOpenChange, createAgent, fetchEngineCatalog, availableEngines, onCreated }: Props) {
  const { t } = useLocale()
  const navigate = useNavigate()
  const { machines, status: machinesStatus, fetchMachines } = useMachines()
  const { projects, rooms: roomsByProject, status: roomsStatus, refetch: refetchRooms } = useRooms()
  const onlineMachines = useMemo(() => machines.filter(m => m.status === 'online'), [machines])
  const [machineId, setMachineId] = useState<string | null>(null)

  // Machine status changes often; refresh each time the dialog opens.
  useEffect(() => {
    if (open) void fetchMachines().catch(() => {})
  }, [open, fetchMachines])

  useEffect(() => {
    setMachineId(current => onlineMachines.some(m => m.id === current) ? current : onlineMachines[0]?.id ?? null)
  }, [onlineMachines])

  const { data: detail, status: detailStatus } = useMachineDetail(open ? machineId : null, '')
  const engines = useMemo(() => {
    const deprecated = new Set(availableEngines.filter(e => e.deprecated).map(e => e.engine))
    const list = detail?.engines ?? []
    return [...list.filter(e => !deprecated.has(e.engine)), ...list.filter(e => deprecated.has(e.engine))]
  }, [detail, availableEngines])

  // A refresh keeps the last known machines, so an open form is never torn down mid-edit.
  if (!machineId) {
    const failed = machinesStatus === 'error'
    const empty = machinesStatus === 'loaded'
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('admin.machines.createAgent')}</DialogTitle>
            <DialogDescription role={failed ? 'alert' : 'status'}>
              {failed ? t('chat.addAgentMachinesFailed') : empty ? t('chat.addAgentNoMachine') : t('common.loading')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
            {failed && <Button onClick={() => void fetchMachines().catch(() => {})}>{t('common.retry')}</Button>}
            {empty && <Button onClick={() => { onOpenChange(false); navigate('/admin/machines') }}>{t('chat.goToMachines')}</Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  const machine = onlineMachines.find(m => m.id === machineId)
  return (
    <CreateAgentDialog
      open={open}
      onOpenChange={onOpenChange}
      machineId={machineId}
      machineName={machine?.name ?? ''}
      machineOptions={onlineMachines.map(m => ({ id: m.id, name: m.name }))}
      onMachineChange={setMachineId}
      enginesLoading={detailStatus === 'loading'}
      engines={engines}
      availableEngines={availableEngines}
      projects={projects}
      roomsByProject={roomsByProject}
      roomsStatus={roomsStatus}
      onRetryRooms={refetchRooms}
      createAgent={createAgent}
      fetchEngineCatalog={fetchEngineCatalog}
      onCreated={onCreated}
    />
  )
}
