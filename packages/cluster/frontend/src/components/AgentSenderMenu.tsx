import { useRef, useState, type ReactNode } from 'react'
import { MessageSquare, Settings } from 'lucide-react'
import SidebarMenuPopover from '@/components/SidebarMenuPopover'
import { requestAgentAction, type AgentAction } from '@/lib/agentActions'
import { useLocale } from '@/i18n/LocaleProvider'

/**
 * Sender chrome (avatar + name) of an agent's chat message, turned into
 * a menu trigger: open a direct message with the agent or its settings.
 */
export default function AgentSenderMenu({
  agentId,
  name,
  children,
}: {
  agentId: string
  name: string
  children: ReactNode
}) {
  const { t } = useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const label = t('admin.agentSettings.avatarMenu', { name })

  const run = (action: AgentAction) => {
    setOpen(false)
    requestAgentAction(agentId, action)
  }

  const itemClass =
    'flex min-h-[var(--control-height)] w-full items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer'

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        title={label}
        data-testid="message-sender-menu-trigger"
        className="-mx-1 flex items-center gap-1.5 rounded-[var(--radius-sm)] px-1 py-0.5 hover:bg-[var(--color-surface-hover)] aria-expanded:bg-[var(--color-surface-hover)]"
      >
        {children}
      </button>
      {open && (
        <SidebarMenuPopover
          anchorRef={rootRef}
          label={label}
          onClose={() => setOpen(false)}
          align="start"
        >
          <ul className="py-1">
            <li>
              <button
                type="button"
                onClick={() => run('direct-message')}
                data-testid="message-sender-menu-dm"
                className={itemClass}
              >
                <MessageSquare className="h-4 w-4" />
                <span>{t('chat.directMessage')}</span>
              </button>
            </li>
            <li>
              <button
                type="button"
                onClick={() => run('settings')}
                data-testid="message-sender-menu-settings"
                className={itemClass}
              >
                <Settings className="h-4 w-4" />
                <span>{t('admin.agentSettings.settingsAction')}</span>
              </button>
            </li>
          </ul>
        </SidebarMenuPopover>
      )}
    </div>
  )
}
