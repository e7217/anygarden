import { useLocale } from '@/i18n/LocaleProvider'

interface UpdateDotProps {
  className?: string
}

export default function UpdateDot({ className = '' }: UpdateDotProps) {
  const { t } = useLocale()
  return (
    <span
      role="status"
      aria-label={t('common.unreadUpdates')}
      title={t('common.unreadUpdates')}
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-brand)] ${className}`}
    />
  )
}
