import PageShell from '@/components/PageShell'
import AdminMCPTemplates from '@/components/AdminMCPTemplates'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminMCPTemplatesPage() {
  const { t } = useLocale()
  return (
    <PageShell title={t('admin.mcp.title')}>
      <AdminMCPTemplates />
    </PageShell>
  )
}
