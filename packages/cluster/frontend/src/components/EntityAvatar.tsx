import type { CSSProperties } from 'react'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { EngineGlyph } from '@/components/EngineGlyph'
import { getAvatarTone, getInitials } from '@/lib/avatar'
import { lookupLucideIcon } from '@/lib/avatar-options'
import { cn } from '@/lib/utils'

export type EntityAvatarSize = 'xs' | 'sm' | 'md' | 'lg'
export type EntityKind = 'user' | 'agent' | 'guest' | 'room'

/** Issue #101 — server-stored avatar override kinds. ``null`` /
 *  undefined means "no customization, fall back to initials". */
export type AvatarKind = 'emoji' | 'lucide'

interface EntityAvatarProps {
  /** Stable identifier used as the tone seed. Same id → same color. */
  id: string
  /** Display name. First letter (or CJK character) becomes the fallback. */
  name: string
  kind: EntityKind
  /** Required when kind='agent' to render the engine-mark overlay badge. */
  engine?: string
  /** xs=20 · sm=24 · md=32 · lg=40 (pixels). Default: 'sm'. */
  size?: EntityAvatarSize
  /** Future extension: loaded image. Falls through to initials on error. */
  imageUrl?: string
  /** Issue #101 — admin-chosen avatar override kind. */
  avatarKind?: AvatarKind | null
  /** Issue #101 — payload for ``avatarKind``:
   *  - ``'emoji'``  → the emoji character itself (e.g. ``"🤖"``)
   *  - ``'lucide'`` → a lucide-react component name (e.g. ``"Rocket"``)
   *    that exists in ``LUCIDE_COMPONENTS``. Unknown names fall
   *    back to initials so stale server values don't crash the UI. */
  avatarValue?: string | null
  className?: string
  'data-testid'?: string
}

const SIZE_PX: Record<EntityAvatarSize, number> = {
  xs: 20,
  sm: 24,
  md: 32,
  lg: 40,
}

// Glyph diameter for the overlay badge. The wrapping pill gets +4px
// for the white ring that separates the glyph from the avatar fill.
const GLYPH_PX: Record<EntityAvatarSize, number> = {
  xs: 9,
  sm: 11,
  md: 14,
  lg: 16,
}

const INITIAL_CLASS: Record<EntityAvatarSize, string> = {
  xs: 'text-[9px]',
  sm: 'text-[10px]',
  md: 'text-xs',
  lg: 'text-sm',
}

// Font-size for the emoji override. Slightly smaller than the avatar
// diameter so the emoji does not touch the pill edge.
const EMOJI_PX: Record<EntityAvatarSize, number> = {
  xs: 12,
  sm: 14,
  md: 18,
  lg: 22,
}

// Lucide glyph diameter when used as the avatar body (not the
// engine-badge overlay).
const LUCIDE_PX: Record<EntityAvatarSize, number> = {
  xs: 12,
  sm: 14,
  md: 18,
  lg: 22,
}

/**
 * Seed-driven avatar used for users, agents, guests, and rooms.
 *
 * Design notes:
 * - The tone is deterministic in ``id`` so the same entity renders
 *   the same color across every surface (sidebar · chat · dialogs).
 * - For agents, the engine (Claude / Codex / Gemini / …) is surfaced
 *   as a small badge in the bottom-right corner so the avatar doubles
 *   as an engine chip without forcing callers to stack a second glyph.
 * - Guests get a dashed border rather than a distinct tone so the
 *   "tentative / anonymous" affordance is readable independent of
 *   the hash-assigned color.
 * - Presence is intentionally not baked in; callers that already
 *   render ``<PresenceDot/>`` simply place it adjacent to the avatar
 *   to avoid double-signaling.
 *
 * Issue #101 — body render order (falls through on miss):
 *   imageUrl → emoji → lucide → initials.
 * Background tone and engine-glyph overlay are common to every
 * branch, so "same agent = same color" stays intact regardless of
 * whether the admin has customized the body.
 */
export function EntityAvatar({
  id,
  name,
  kind,
  engine,
  size = 'sm',
  imageUrl,
  avatarKind,
  avatarValue,
  className,
  'data-testid': testId,
}: EntityAvatarProps) {
  const tone = getAvatarTone(id)
  const initials = getInitials(name)
  const px = SIZE_PX[size]
  const glyphPx = GLYPH_PX[size]
  const badgePx = glyphPx + 4
  const isGuest = kind === 'guest'
  const showGlyph = kind === 'agent' && !!engine

  // Resolve the body render branch once so JSX stays legible.
  // ``imageUrl`` already has its own ``<AvatarImage>`` path inside
  // shadcn's Avatar; we only need to decide which fallback body to
  // render (emoji, lucide glyph, or initials).
  const emojiBody = avatarKind === 'emoji' && avatarValue ? avatarValue : null
  const LucideIcon =
    avatarKind === 'lucide' ? lookupLucideIcon(avatarValue) : null

  const wrapperStyle: CSSProperties = {
    width: px,
    height: px,
  }

  return (
    <span
      className={cn('relative inline-block shrink-0', className)}
      style={wrapperStyle}
      data-testid={testId}
      data-guest={isGuest ? 'true' : undefined}
      aria-label={`${kind} ${name}`}
    >
      <Avatar
        className={cn(
          'h-full w-full',
          isGuest && 'border border-dashed border-[var(--color-brand)]',
        )}
        style={{ backgroundColor: tone.bg }}
      >
        {imageUrl && <AvatarImage src={imageUrl} alt="" />}
        <AvatarFallback
          className={cn('font-medium', INITIAL_CLASS[size])}
          style={{ backgroundColor: 'transparent', color: tone.fg }}
          data-testid="entity-avatar-fallback"
        >
          {emojiBody ? (
            <span
              // Emoji bodies skip the tone color (OS renderer supplies
              // their color) but keep the tone background from Avatar.
              className="leading-none"
              style={{ fontSize: EMOJI_PX[size] }}
              data-testid="entity-avatar-emoji"
              aria-hidden="true"
            >
              {emojiBody}
            </span>
          ) : LucideIcon ? (
            <LucideIcon
              size={LUCIDE_PX[size]}
              color={tone.fg}
              strokeWidth={1.75}
              data-testid="entity-avatar-lucide"
              aria-hidden="true"
            />
          ) : (
            initials
          )}
        </AvatarFallback>
      </Avatar>
      {showGlyph && (
        <span
          className="absolute -bottom-0.5 -right-0.5 flex items-center justify-center rounded-full bg-white border border-[var(--color-border)]"
          style={{ width: badgePx, height: badgePx }}
          data-testid="entity-avatar-engine-glyph"
          aria-hidden="true"
        >
          <EngineGlyph engine={engine} size={glyphPx} />
        </span>
      )}
    </span>
  )
}
