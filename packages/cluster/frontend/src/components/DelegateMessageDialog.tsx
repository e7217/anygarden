import { useState } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { uuid, type FederationMessage } from '@/lib/federationApi'
import { sharedErrorCopy, targetUnavailableCopy } from '@/lib/sharedRoomCopy'
import type { SharedRoomState } from '@/hooks/useSharedRoom'

export default function DelegateMessageDialog({ message, room, disabled }: {
  message: FederationMessage; room: SharedRoomState; disabled: boolean
}) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState('')
  const [requestId, setRequestId] = useState(uuid)
  const targets = room.data?.targets ?? []
  const chosen = targets.find(target => `${target.node_id}:${target.agent_id}` === selected)
  const available = chosen?.can_execute && chosen.name

  function changeOpen(next: boolean) {
    setOpen(next)
    if (next) room.clearActionError()
  }

  async function submit() {
    if (!chosen || !available || disabled) return
    const accepted = await room.delegate({
      request_id: requestId, source_message_id: message.message_id,
      executor: { node_id: chosen.node_id, agent_id: chosen.agent_id },
    })
    if (accepted) {
      setOpen(false)
      setRequestId(uuid())
      setSelected('')
    }
  }

  return (
    <Dialog open={open} onOpenChange={changeOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" disabled={disabled}>{t('federation.room.delegate')}</Button>
      </DialogTrigger>
      <DialogContent onCloseAutoFocus={event => {
        if (!disabled) return
        const source = document.getElementById(`shared-message-${message.message_id}`)
        if (source) { event.preventDefault(); source.focus() }
      }}>
        <DialogHeader>
          <DialogTitle>{t('federation.room.delegate')}</DialogTitle>
          <DialogDescription>{t('federation.room.delegateDescription')}</DialogDescription>
        </DialogHeader>
        <blockquote className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius-sm)] border-l-2 border-[var(--color-brand)] bg-[var(--color-surface-alt)] p-3 text-sm">
          {message.text}
        </blockquote>
        <fieldset className="min-w-0 space-y-2" disabled={room.busy || disabled}>
          <legend className="mb-2 text-sm font-medium">{t('federation.room.target')}</legend>
          {targets.length === 0 && <p className="text-sm text-[var(--color-foreground-muted)]">{t('federation.room.noTargets')}</p>}
          {targets.map(target => {
            const value = `${target.node_id}:${target.agent_id}`
            const named = Boolean(target.name)
            const ready = named && target.can_execute
            return (
              <label key={value} className={`flex min-h-11 items-start gap-3 rounded-[var(--radius-md)] border p-3 text-sm ${ready ? 'cursor-pointer hover:bg-[var(--color-surface-hover)]' : 'text-[var(--color-foreground-muted)]'}`}>
                <input type="radio" className="mt-1 accent-[var(--color-brand)]" name="delegation-target" value={value} checked={selected === value} disabled={!ready}
                  onChange={() => { setSelected(value); setRequestId(uuid()); room.clearActionError() }} />
                <span className="min-w-0 flex-1 break-words">
                  <span className="block font-medium">{target.name || t('federation.room.unknownAgent')}</span>
                  <span className="block text-caption text-[var(--color-foreground-muted)]">{target.is_local ? t('federation.room.currentServer') : target.node_name || (target.server_label ? t('federation.room.serverAddress', { address: target.server_label }) : t('federation.room.unknownServer'))}</span>
                  {target.description && <span className="mt-1 block">{target.description}</span>}
                  <span className="mt-1 block text-caption">{t(!named ? 'federation.room.targetNamePending' : ready ? 'federation.room.targetReady' : targetUnavailableCopy(target.unavailable_code))}</span>
                </span>
              </label>
            )
          })}
        </fieldset>
        {room.actionError && <p role="alert" className="text-sm text-[var(--color-danger)]">{t(sharedErrorCopy(room.actionError))}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => changeOpen(false)}>{t('federation.room.cancel')}</Button>
          <Button disabled={!available || room.busy || disabled} onClick={() => void submit()}>{t('federation.room.confirmDelegate')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
