import PageShell from '@/components/PageShell'
import FederationLiveWorkspace from '@/components/FederationLiveWorkspace'
import { useFederation } from '@/hooks/useFederation'

export default function AdminFederationPage() {
  const federation = useFederation()
  return (
    <PageShell title="Federation">
      <FederationLiveWorkspace federation={federation} />
    </PageShell>
  )
}
