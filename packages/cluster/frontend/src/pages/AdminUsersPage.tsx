import PageShell from '@/components/PageShell'
import AdminUsers from '@/components/AdminUsers'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminUsersPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('users.title')}>
      <AdminUsers />
    </PageShell>
  )
}
