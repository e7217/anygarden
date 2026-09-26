import PageShell from '@/components/PageShell'
import AdminMachines from '@/components/AdminMachines'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminMachinesPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('admin.machines.title')}>
      <AdminMachines />
    </PageShell>
  )
}
