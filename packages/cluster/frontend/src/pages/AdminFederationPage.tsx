import PageShell from '@/components/PageShell'
import FederationLiveWorkspace from '@/components/FederationLiveWorkspace'
import { useFederation } from '@/hooks/useFederation'
import { useLocale } from '@/i18n/LocaleProvider'

export default function AdminFederationPage() {
  const { t } = useLocale()
  const federation = useFederation()
  return (
    <PageShell title={t('navigation.federation')}>
      <FederationLiveWorkspace federation={federation} />
    </PageShell>
  )
}
