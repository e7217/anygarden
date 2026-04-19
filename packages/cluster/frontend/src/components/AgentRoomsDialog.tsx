/**
 * AgentRoomsDialog — thin wrapper around RoomsPanel (#158).
 *
 * After the #158 unification, Sidebar/AdminMachines reach the
 * rooms management UI via the unified AgentSettingsDialog. The
 * topology DetailPanel still opens this dialog directly because
 * its node-detail panel surfaces a focused "manage rooms" intent —
 * forcing that path through the full Settings dialog would be
 * overkill.
 *
 * This file is now a 1-to-1 Dialog shell around RoomsPanel, which
 * owns the actual assigned/available UI. Keeping the wrapper small
 * means the topology view and the settings dialog never diverge on
 * rooms behavior.
 */
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import RoomsPanel from '@/components/agent-settings/RoomsPanel'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  agentId: string | null
  /** Optional callback fired after every mutation so the parent can
   *  refresh its own state (e.g. a machine-detail agent list that
   *  displays the comma-joined room names inline). */
  onChange?: () => void
}

export default function AgentRoomsDialog({ open, onOpenChange, agentId, onChange }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Manage Rooms</DialogTitle>
          <DialogDescription>Assign or remove this agent from rooms.</DialogDescription>
        </DialogHeader>
        {open ? (
          <RoomsPanel agentId={agentId} onChange={onChange} />
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
