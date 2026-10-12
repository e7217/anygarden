import PageShell from '@/components/PageShell'
import AdminBudgets from '@/components/AdminBudgets'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminBudgetsPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('admin.budgets.title')}>
      <AdminBudgets />
    </PageShell>
  )
}
