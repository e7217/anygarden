import { useEffect, useMemo, useRef, useState, type ComponentPropsWithoutRef } from 'react'
import { fetchArtifactBlobUrl, startArtifactDownload } from '@/lib/roomArtifacts'
import { useLocale } from '@/i18n/LocaleProvider'

function localArtifact(href: string | undefined): { roomId: string; artifactId: string } | null {
  if (!href) return null
  try {
    const url = new URL(href, window.location.origin)
    if (url.origin !== window.location.origin) return null
    const match = /^\/api\/v1\/rooms\/([^/]+)\/artifacts\/([^/]+)\/?$/.exec(url.pathname)
    return match ? { roomId: match[1], artifactId: match[2] } : null
  } catch { return null }
}

export default function AuthenticatedArtifactLink({ href, children, ...props }: ComponentPropsWithoutRef<'a'>) {
  const { t } = useLocale()
  const target = localArtifact(href)
  const scope = useMemo(() => ({ active: true, downloading: false }), [href])
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  useEffect(() => { scope.active = true; setError(null); setPending(false); return () => { scope.active = false } }, [scope])
  if (!target) return <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
  return <>
    <a {...props} href={href} aria-busy={pending || undefined} onClick={async event => {
      event.preventDefault()
      if (scope.downloading) return
      scope.downloading = true
      setPending(true)
      setError(null)
      let download: Awaited<ReturnType<typeof fetchArtifactBlobUrl>> | null = null
      try {
        download = await fetchArtifactBlobUrl(target.roomId, target.artifactId)
        if (!scope.active || currentScope.current !== scope) return
        startArtifactDownload(download, download.filename || target.artifactId)
        download = null
      } catch (error) {
        if (scope.active && currentScope.current === scope) setError(error instanceof Error ? error.message : 'HTTP 500')
      } finally {
        download?.revoke()
        scope.downloading = false
        if (scope.active && currentScope.current === scope) setPending(false)
      }
    }}>{children}</a>
    {error && <span role="alert" className="ml-2 text-xs text-[var(--color-danger)]">{t('tasks.requestFailed', { status: error.match(/HTTP (\d+)/)?.[1] ?? '500' })}</span>}
  </>
}
