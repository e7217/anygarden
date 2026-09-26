import PageShell from '@/components/PageShell'
import { UsageSection } from '@/components/UsageSection'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminUsagePage() {
  const { t } = useLocale()
  return <PageShell title={t('admin.usage.title')}><UsageSection /></PageShell>
}
