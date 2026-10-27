import { useMemo } from 'react'
import CampaignForm from '@/components/campaign/CampaignForm'
import { useFeedback } from '@/components/feedback/FeedbackProvider'
import GoalForm from '@/components/goal-form/GoalForm'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { useLocale } from '@/i18n/LocaleProvider'
import { createCampaign } from '@/lib/campaigns'
import { routineAgentOptions } from '@/lib/goals'
import type { Participant } from '@/lib/participants'
import type { SlashFormRequest } from '@/lib/slashCommands'

interface SlashFormDialogProps {
  roomId: string
  participants: Record<string, Participant>
  /** The command to answer; null keeps both forms closed. */
  request: SlashFormRequest | null
  onClose: () => void
}

/**
 * #940 — the forms ``/routine`` and ``/goal`` open from the composer. They
 * reuse the right-rail forms so a routine or goal made here is the same as
 * one made there. The caller checks first that the form can open
 * (agents in the room for a routine, ``canStartCampaign`` for a goal).
 */
export default function SlashFormDialog({ roomId, participants, request, onClose }: SlashFormDialogProps) {
  const { t } = useLocale()
  const { notify } = useFeedback()
  const agents = useMemo(() => routineAgentOptions(participants), [participants])

  const routine = request?.command === 'routine' ? request.payload : null
  const goal = request?.command === 'goal' ? request.payload : null
  const assignee = routine?.assignee_pid ? participants[routine.assignee_pid]?.agent_id ?? null : null

  return (
    <>
      <Dialog open={routine !== null} onOpenChange={open => { if (!open) onClose() }}>
        <DialogContent className="max-w-md max-h-[min(90dvh,52rem)] overflow-y-auto" data-testid="slash-routine-dialog">
          <DialogHeader>
            <DialogTitle>{t('chat.slash.routineTitle')}</DialogTitle>
            <DialogDescription>{t('chat.slash.routineDescription')}</DialogDescription>
          </DialogHeader>
          {routine && (
            <GoalForm
              roomAgents={agents}
              defaultAgentId={assignee}
              defaultReportRoomId={roomId}
              defaultTitle={routine.title}
              onCreated={() => {
                // The rail's routine list refreshes on this event.
                window.dispatchEvent(new Event('anygarden:goal:updated'))
                onClose()
              }}
              onCancel={onClose}
            />
          )}
        </DialogContent>
      </Dialog>
      <CampaignForm
        open={goal !== null}
        onOpenChange={open => { if (!open) onClose() }}
        onSubmit={input => createCampaign(roomId, input)}
        onCreated={() => notify({ message: t('campaigns.form.created') })}
        initialTitle={goal?.title ?? ''}
      />
    </>
  )
}
