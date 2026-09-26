import PageShell from '@/components/PageShell'
import AdminSkills from '@/components/AdminSkills'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminSkillsPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('admin.skills.title')}>
      <AdminSkills />
    </PageShell>
  )
}
