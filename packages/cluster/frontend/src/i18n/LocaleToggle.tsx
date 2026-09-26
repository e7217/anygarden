import { useLocale, type Locale } from './LocaleProvider'

export function LocaleToggle({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useLocale()
  return (
    <div role="group" aria-label={t('common.language')} className="inline-flex shrink-0 items-center gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-0.5">
      {(['ko', 'en'] as const).map((value: Locale) => (
        <button
          key={value}
          type="button"
          lang={value}
          aria-label={value === 'ko' ? t('common.korean') : t('common.english')}
          aria-pressed={locale === value}
          onClick={() => setLocale(value)}
          className={`h-[var(--control-height)] min-w-[var(--control-height)] rounded-[var(--radius-sm)] px-2 text-xs font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-[var(--color-brand-focus)] ${
            locale === value
              ? 'bg-[var(--color-surface)] text-[var(--color-foreground)] shadow-whisper'
              : 'text-[var(--color-foreground-muted)] hover:text-[var(--color-foreground)]'
          }`}
        >
          {compact ? value.toUpperCase() : value === 'ko' ? '한국어' : 'English'}
        </button>
      ))}
    </div>
  )
}
