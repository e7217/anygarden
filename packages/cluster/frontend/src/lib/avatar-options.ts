/**
 * Curated avatar choices for the agent avatar picker (Issue #101).
 *
 * Two motivations shape what's here:
 *
 * 1. Emojis and lucide-react icons are the Phase-1 sources (image
 *    upload and @lobehub brand marks are deferred to later phases),
 *    so this module defines exactly those two catalogs.
 *
 * 2. We explicitly hard-code a short list of lucide icons — not
 *    the full ~1500 — so the bundle only pulls in the handful we
 *    actually surface. Adding an icon to ``CURATED_LUCIDE_NAMES``
 *    means remembering to import and wire it into
 *    ``LUCIDE_COMPONENTS`` below. Missing-map entries degrade
 *    gracefully: ``EntityAvatar`` falls back to initials for any
 *    name it can't resolve (same as an unknown server-stored
 *    value).
 */
import type { LucideIcon } from 'lucide-react'
import {
  Beaker,
  Book,
  Bot,
  Brain,
  Camera,
  Code,
  Compass,
  Coffee,
  Cpu,
  Eye,
  Feather,
  FileText,
  Flag,
  FlaskConical,
  Gavel,
  Globe,
  Grid3x3,
  Heart,
  Key,
  Layers,
  Library,
  Lock,
  Map,
  MessageSquare,
  Mic,
  Paintbrush,
  Palette,
  Pencil,
  Rocket,
  Scale,
  Search,
  Shield,
  Sparkles,
  Star,
  Target,
  Terminal,
  TestTube,
  TreePine,
  Wrench,
  Zap,
} from 'lucide-react'

/**
 * Forty role-flavored lucide icons grouped loosely by theme. Keeping
 * this under fifty keeps the picker grid scannable at a glance; the
 * full library is out of scope for Phase 1 (tracked on the issue).
 */
export const CURATED_LUCIDE_NAMES = [
  // Agent archetypes
  'Bot', 'Brain', 'Cpu', 'Wrench', 'Code', 'Terminal', 'Globe', 'Zap', 'Target', 'Rocket',
  // Knowledge work
  'Search', 'FileText', 'Book', 'Library', 'MessageSquare', 'Mic', 'Eye', 'Camera', 'Map', 'Compass',
  // Research & making
  'FlaskConical', 'TestTube', 'Beaker', 'Pencil', 'Feather', 'Paintbrush', 'Palette', 'Layers', 'Grid3x3', 'Sparkles',
  // Stewardship
  'Heart', 'Star', 'Flag', 'Shield', 'Lock', 'Key', 'Scale', 'Gavel', 'Coffee', 'TreePine',
] as const

export type CuratedLucideName = (typeof CURATED_LUCIDE_NAMES)[number]

/**
 * Component map used by ``EntityAvatar`` to render a stored lucide
 * name. Keys are in lockstep with ``CURATED_LUCIDE_NAMES`` — a name
 * that is in the list but missing from the map (or vice versa) is a
 * programming error and surfaces as a test failure below.
 */
export const LUCIDE_COMPONENTS: Record<CuratedLucideName, LucideIcon> = {
  Bot, Brain, Cpu, Wrench, Code, Terminal, Globe, Zap, Target, Rocket,
  Search, FileText, Book, Library, MessageSquare, Mic, Eye, Camera, Map, Compass,
  FlaskConical, TestTube, Beaker, Pencil, Feather, Paintbrush, Palette, Layers, Grid3x3, Sparkles,
  Heart, Star, Flag, Shield, Lock, Key, Scale, Gavel, Coffee, TreePine,
}

/**
 * Forty-eight role-flavored emoji. Kept small so the picker grid
 * stays an at-a-glance choice. OS renderer decides the exact glyph —
 * consistency across user devices is accepted as imperfect for
 * Phase 1 (Twemoji-style normalization would be a separate issue).
 */
export const CURATED_EMOJIS: readonly string[] = [
  '🤖', '🧠', '🛠️', '🔧', '💻', '⚡', '🎯', '🚀', '🔍', '📚',
  '📝', '💬', '📞', '🎤', '👀', '📷', '🗺️', '🧭', '🧪', '⚗️',
  '🧬', '✏️', '🖋️', '🎨', '🖌️', '🧱', '✨', '⭐', '🔥', '🌟',
  '❤️', '💡', '🔑', '🔒', '🛡️', '⚖️', '☕', '🌳', '🐱', '🐶',
  '🦊', '🦁', '🐼', '🐙', '🦄', '🌈', '🍀', '🧩',
]

/**
 * Look up a lucide component by stored name. Returns ``null`` when
 * the name is unknown (e.g. an older client saved a name that has
 * since been removed from the curated list, or a direct API call
 * put something arbitrary there). Callers fall back to initials.
 */
export function lookupLucideIcon(name: string | null | undefined): LucideIcon | null {
  if (!name) return null
  return (LUCIDE_COMPONENTS as Record<string, LucideIcon>)[name] ?? null
}
