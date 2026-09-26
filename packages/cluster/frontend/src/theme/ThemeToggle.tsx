import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useLocale } from '@/i18n/LocaleProvider'
import { useTheme } from './ThemeProvider'

type ThemeToggleProps = {
  className?: string
  showLabel?: boolean
}

export function ThemeToggle({ className, showLabel = false }: ThemeToggleProps) {
  const { theme, toggleTheme } = useTheme()
  const { t } = useLocale()
  const isDark = theme === 'dark'
  const title = isDark ? t('common.switchToLightMode') : t('common.switchToDarkMode')

  return (
    <Button
      type="button"
      variant="ghost"
      size={showLabel ? 'default' : 'icon'}
      className={className}
      onClick={toggleTheme}
      aria-label={t('common.darkMode')}
      aria-pressed={isDark}
      title={title}
    >
      {isDark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
      {showLabel && <span>{t('common.darkMode')}</span>}
    </Button>
  )
}
