import PageShell from '@/components/PageShell'
import AdminSystem from '@/components/AdminSystem'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminSystemPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('admin.system.title')}>
      <AdminSystem />
    </PageShell>
  )
}
