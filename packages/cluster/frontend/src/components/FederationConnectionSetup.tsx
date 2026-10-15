import { useId, useState } from 'react'
import { Check, Copy, ShieldCheck } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import type {
  ConnectionCardPreview,
  DialPreview,
  EndpointInput,
  InviteCodePreview,
} from '@/lib/federationApi'
import type { useFederation } from '@/hooks/useFederation'
import { useLocale } from '@/i18n/LocaleProvider'
import type { MessageKey } from '@/i18n/messages'

type Federation = ReturnType<typeof useFederation>

interface SetupProps {
  federation: Federation
  /** The last action error when this card started that action, else null. */
  error?: string | null
  /** Called before this card starts an action, so its error shows here. */
  onAction?: () => void
}

/**
 * #839 — connect two nodes by exchanging two pasteable strings: this node's
 * connection card, then the invite code. The server decodes both and only
 * *suggests* the IPs to dial; the admin confirms them, opts into private
 * addresses explicitly, and confirms the fingerprint out of band.
 */

const hint = 'text-sm text-[var(--color-foreground-muted)]'

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

const ERROR_KEYS: Record<string, MessageKey> = {
  CODE_INVALID: 'federation.connect.error.CODE_INVALID',
  IDENTITY_MISMATCH: 'federation.connect.error.IDENTITY_MISMATCH',
  CERTIFICATE_DENIED: 'federation.connect.error.CERTIFICATE_DENIED',
  ENDPOINT_DENIED: 'federation.connect.error.ENDPOINT_DENIED',
  PUBLIC_URL_REQUIRED: 'federation.connect.error.PUBLIC_URL_REQUIRED',
  PUBLIC_URL_INVALID: 'federation.connect.error.PUBLIC_URL_INVALID',
  INVITE_CONSUMED_OR_EXPIRED: 'federation.connect.error.INVITE_CONSUMED_OR_EXPIRED',
  PEER_UNAVAILABLE: 'federation.connect.error.PEER_UNAVAILABLE',
}

/** A failed setup action, explained for the codes admins can act on. */
export function ConnectionError({ error }: { error: string | null }) {
  const { t } = useLocale()
  if (!error) return null
  const key = ERROR_KEYS[error.split(' ')[0]]
  return (
    <p role="alert" className="text-sm text-[var(--color-danger)]">
      {t('federation.errorPrefix')} · {key ? `${t(key)} (${error})` : error}
    </p>
  )
}

/** SHA-256 hex in groups of four so two admins can read it aloud. */
export function Fingerprint({ value }: { value: string }) {
  const groups = value.match(/.{1,4}/g) ?? [value]
  return (
    <code className="block break-words font-mono text-sm leading-6 text-[var(--color-foreground)]">
      {groups.join(' ')}
    </code>
  )
}

function CopyableCode({ label, value }: { label: string; value: string }) {
  const { t } = useLocale()
  const id = useId()
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      // Plain-http LAN pages have no clipboard API: select for manual copy.
      const area = document.getElementById(id) as HTMLTextAreaElement | null
      area?.select()
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        <Button size="sm" variant="outline" onClick={() => void copy()}>
          {copied ? <Check className="mr-1 size-4" aria-hidden="true" /> : <Copy className="mr-1 size-4" aria-hidden="true" />}
          {copied ? t('federation.connect.copied') : t('federation.connect.copy')}
        </Button>
      </div>
      <Textarea id={id} readOnly value={value} className="min-h-20 font-mono text-sm" onFocus={(e) => e.currentTarget.select()} />
    </div>
  )
}

interface DialApproval {
  ips: string
  allowPrivate: boolean
  fingerprintConfirmed: boolean
}

function approvalFrom(preview: DialPreview): DialApproval {
  return { ips: preview.suggested_ips.join(', '), allowPrivate: false, fingerprintConfirmed: false }
}

function endpointFrom(url: string, approval: DialApproval): EndpointInput {
  return {
    url,
    approved_ips: approval.ips
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
    allow_private: approval.allowPrivate,
  }
}

/** The part both sides share: whose node, which fingerprint, which IPs. */
function PeerReview({
  nodeId,
  fingerprint,
  url,
  preview,
  approval,
  onChange,
}: {
  nodeId: string
  fingerprint: string
  url: string
  preview: DialPreview
  approval: DialApproval
  onChange: (next: DialApproval) => void
}) {
  const { t } = useLocale()
  const ids = { ips: useId(), ipsHint: useId(), priv: useId(), privHint: useId(), confirm: useId() }
  return (
    <div className="space-y-4 rounded-[var(--radius-md)] border border-[var(--color-border)] bg-[var(--color-surface-alt)] p-4">
      <dl className="grid gap-3 text-sm sm:grid-cols-[max-content_1fr] sm:gap-x-4">
        <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.nodeId')}</dt>
        <dd className="break-all font-mono">{nodeId}</dd>
        <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.address')}</dt>
        <dd className="break-all font-mono">{url}</dd>
        <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.fingerprint')}</dt>
        <dd><Fingerprint value={fingerprint} /></dd>
      </dl>

      <Field label={t('federation.connect.approvedIps')} htmlFor={ids.ips}>
        <Input
          id={ids.ips}
          value={approval.ips}
          aria-describedby={ids.ipsHint}
          onChange={(e) => onChange({ ...approval, ips: e.target.value })}
          placeholder="192.0.2.10"
        />
        <p id={ids.ipsHint} className={hint}>
          {preview.suggested_ips.length > 0
            ? t('federation.connect.approvedIpsSuggested')
            : t('federation.connect.approvedIpsUnresolved')}
        </p>
      </Field>

      <div className="flex items-start gap-3">
        <input
          id={ids.priv}
          type="checkbox"
          checked={approval.allowPrivate}
          onChange={(e) => onChange({ ...approval, allowPrivate: e.target.checked })}
          aria-describedby={ids.privHint}
          className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
        />
        <div className="min-w-0 space-y-1">
          <Label htmlFor={ids.priv}>{t('federation.connect.allowPrivate')}</Label>
          <p id={ids.privHint} className={preview.requires_private ? 'text-sm text-[var(--color-warning)]' : hint}>
            {preview.requires_private
              ? t('federation.connect.allowPrivateRequired')
              : t('federation.connect.allowPrivateHint')}
          </p>
        </div>
      </div>

      <div className="flex items-start gap-3">
        <input
          id={ids.confirm}
          type="checkbox"
          checked={approval.fingerprintConfirmed}
          onChange={(e) => onChange({ ...approval, fingerprintConfirmed: e.target.checked })}
          className="mt-0.5 size-4 shrink-0 accent-[var(--color-brand)]"
        />
        <Label htmlFor={ids.confirm} className="leading-5">
          {t('federation.connect.confirmFingerprint')}
        </Label>
      </div>
    </div>
  )
}

function readyToDial(approval: DialApproval): boolean {
  return approval.fingerprintConfirmed && approval.ips.trim().length > 0
}

export function NodeConnectionCard({ federation, error = null, onAction }: SetupProps) {
  const { t, formatDate } = useLocale()
  const identity = federation.nodeIdentity
  const urlId = useId()
  const [url, setUrl] = useState<string | null>(null)
  const [card, setCard] = useState<string | null>(null)
  const address = url ?? identity?.public_url ?? ''

  async function create() {
    onAction?.()
    const result = await federation.createConnectionCard(address.trim() || undefined)
    if (result) setCard(result.card)
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{t('federation.connect.myCard')}</CardTitle>
        <CardDescription>{t('federation.connect.myCardDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {identity ? (
          <dl className="grid gap-3 text-sm sm:grid-cols-[max-content_1fr] sm:gap-x-4">
            <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.nodeId')}</dt>
            <dd className="break-all font-mono">{identity.node_id}</dd>
            <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.fingerprint')}</dt>
            <dd><Fingerprint value={identity.fingerprint} /></dd>
            <dt className="text-[var(--color-foreground-muted)]">{t('federation.connect.expires')}</dt>
            <dd>{formatDate(new Date(identity.certificate_expires_at), { dateStyle: 'medium' })}</dd>
          </dl>
        ) : (
          <p className={hint}>{t('federation.connect.identityUnavailable')}</p>
        )}
        <Field label={t('federation.connect.myAddress')} htmlFor={urlId}>
          <Input
            id={urlId}
            value={address}
            onChange={(e) => {
              setUrl(e.target.value)
              setCard(null)
            }}
            placeholder="https://192.0.2.10:8451"
          />
          <p className={hint}>{t('federation.connect.myAddressHint')}</p>
        </Field>
        <div className="flex justify-end">
          <Button
            disabled={federation.nodesCapability !== 'ready' || federation.busy || !address.trim()}
            onClick={() => void create()}
          >
            {t('federation.connect.createCard')}
          </Button>
        </div>
        <ConnectionError error={error} />
        {card && <CopyableCode label={t('federation.connect.cardToShare')} value={card} />}
      </CardContent>
    </Card>
  )
}

export function InviteComposer({ federation, error = null, onAction }: SetupProps) {
  const { t } = useLocale()
  const ids = { card: useId(), issuer: useId(), channel: useId(), capabilities: useId() }
  const [cardText, setCardText] = useState('')
  const [peer, setPeer] = useState<ConnectionCardPreview | null>(null)
  const [approval, setApproval] = useState<DialApproval | null>(null)
  const [issuerUrl, setIssuerUrl] = useState<string | null>(null)
  const [channelId, setChannelId] = useState('')
  const [capabilities, setCapabilities] = useState('channel.read,message.send,task.request')
  const [created, setCreated] = useState<{ code: string | null; bundle: string } | null>(null)
  const issuer = issuerUrl ?? federation.nodeIdentity?.public_url ?? ''

  async function review() {
    onAction?.()
    const preview = await federation.inspectConnectionCard(cardText)
    setPeer(preview)
    setApproval(preview ? approvalFrom(preview) : null)
    setCreated(null)
  }

  async function submit() {
    if (!peer || !approval) return
    onAction?.()
    const result = await federation.createInvite({
      intended_node_id: peer.node_id,
      certificate_pem: peer.certificate_pem,
      endpoint: endpointFrom(peer.url, approval),
      issuer_url: issuer.trim(),
      scopes: [
        {
          channel_id: channelId.trim(),
          actors: [
            {
              node_id: peer.node_id,
              kind: 'human',
              principal_id: '00000000-0000-0000-0000-000000000000',
            },
          ],
          capabilities: capabilities
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
          role: 'member',
        },
      ],
    })
    if (result) {
      setCreated({ code: result.invite_code, bundle: JSON.stringify(result, null, 2) })
    }
  }

  const canSubmit =
    federation.nodesCapability === 'ready' &&
    !federation.busy &&
    peer !== null &&
    approval !== null &&
    readyToDial(approval) &&
    issuer.trim().length > 0 &&
    channelId.trim().length > 0

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{t('federation.createInvitation')}</CardTitle>
        <CardDescription>{t('federation.connect.inviteDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label={t('federation.connect.peerCard')} htmlFor={ids.card}>
          <Textarea
            id={ids.card}
            className="min-h-20 font-mono text-sm"
            value={cardText}
            onChange={(e) => {
              setCardText(e.target.value)
              setPeer(null)
              setApproval(null)
            }}
            placeholder="agpeer1.…"
          />
        </Field>
        <div className="flex justify-end">
          <Button
            variant="outline"
            disabled={federation.nodesCapability !== 'ready' || federation.busy || !cardText.trim()}
            onClick={() => void review()}
          >
            <ShieldCheck className="mr-1 size-4" aria-hidden="true" /> {t('federation.connect.review')}
          </Button>
        </div>

        {peer && approval && (
          <>
            <PeerReview
              nodeId={peer.node_id}
              fingerprint={peer.fingerprint}
              url={peer.url}
              preview={peer}
              approval={approval}
              onChange={setApproval}
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('federation.connect.myAddress')} htmlFor={ids.issuer}>
                <Input
                  id={ids.issuer}
                  value={issuer}
                  onChange={(e) => setIssuerUrl(e.target.value)}
                  placeholder="https://192.0.2.10:8451"
                />
              </Field>
              <Field label={t('federation.channelId')} htmlFor={ids.channel}>
                <Input id={ids.channel} value={channelId} onChange={(e) => setChannelId(e.target.value)} placeholder="uuid" />
              </Field>
            </div>
            <Field label={t('federation.capabilities')} htmlFor={ids.capabilities}>
              <Input id={ids.capabilities} value={capabilities} onChange={(e) => setCapabilities(e.target.value)} />
            </Field>
            <div className="flex justify-end">
              <Button disabled={!canSubmit} onClick={() => void submit()}>
                {t('federation.createInvitation')}
              </Button>
            </div>
          </>
        )}


        <ConnectionError error={error} />

        {created && (
          <>
            {created.code ? (
              <CopyableCode label={t('federation.connect.inviteCodeToShare')} value={created.code} />
            ) : (
              <CopyableCode label={t('federation.bundleCopyNow')} value={created.bundle} />
            )}
            <p className="text-sm text-[var(--color-warning)]">{t('federation.connect.inviteCodeSecret')}</p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function InviteAcceptor({ federation, error = null, onAction }: SetupProps) {
  const { t, formatDate } = useLocale()
  const codeId = useId()
  const [codeText, setCodeText] = useState('')
  const [invite, setInvite] = useState<InviteCodePreview | null>(null)
  const [approval, setApproval] = useState<DialApproval | null>(null)
  const [accepted, setAccepted] = useState(false)

  async function review() {
    onAction?.()
    const preview = await federation.inspectInviteCode(codeText)
    setInvite(preview)
    setApproval(preview ? approvalFrom(preview) : null)
    setAccepted(false)
  }

  async function submit() {
    if (!invite || !approval) return
    onAction?.()
    const result = await federation.acceptInvite({
      bundle: invite.bundle,
      issuer_endpoint: endpointFrom(invite.issuer_url, approval),
    })
    if (result !== null) setAccepted(true)
  }

  const canSubmit =
    federation.nodesCapability === 'ready' && !federation.busy && approval !== null && readyToDial(approval)

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{t('federation.acceptInvitation')}</CardTitle>
        <CardDescription>{t('federation.connect.acceptDescription')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label={t('federation.connect.inviteCode')} htmlFor={codeId}>
          <Textarea
            id={codeId}
            className="min-h-20 font-mono text-sm"
            value={codeText}
            onChange={(e) => {
              setCodeText(e.target.value)
              setInvite(null)
              setApproval(null)
            }}
            placeholder="aginvite1.…"
          />
        </Field>
        <div className="flex justify-end">
          <Button
            variant="outline"
            disabled={federation.nodesCapability !== 'ready' || federation.busy || !codeText.trim()}
            onClick={() => void review()}
          >
            <ShieldCheck className="mr-1 size-4" aria-hidden="true" /> {t('federation.connect.review')}
          </Button>
        </div>

        {invite && approval && (
          <>
            <PeerReview
              nodeId={invite.issuer_node_id}
              fingerprint={invite.issuer_fingerprint}
              url={invite.issuer_url}
              preview={invite}
              approval={approval}
              onChange={setApproval}
            />
            <p className={hint}>
              {t('federation.connect.inviteExpires', {
                date: formatDate(new Date(invite.bundle.expires_at), { dateStyle: 'medium', timeStyle: 'short' }),
              })}
            </p>
            <div className="flex items-center justify-between gap-3">
              {accepted && (
                <p className="flex items-center gap-1 text-sm text-[var(--color-success)]">
                  <Check className="size-4" aria-hidden="true" /> {t('federation.acceptedGrants')}
                </p>
              )}
              <Button className="ml-auto" disabled={!canSubmit} onClick={() => void submit()}>
                {t('federation.acceptInvitation')}
              </Button>
            </div>
          </>
        )}

        <ConnectionError error={error} />

      </CardContent>
    </Card>
  )
}
