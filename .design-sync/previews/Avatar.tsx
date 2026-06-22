// Authored preview for Avatar — seeded-tone fallbacks + sizes.
import { Avatar, AvatarFallback } from 'anygarden-frontend';

export const Fallbacks = () => (
  <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
    <Avatar><AvatarFallback style={{ background: 'var(--color-tone-2)', color: 'var(--color-tone-2-fg)' }}>OR</AvatarFallback></Avatar>
    <Avatar><AvatarFallback style={{ background: 'var(--color-tone-4)', color: 'var(--color-tone-4-fg)' }}>CX</AvatarFallback></Avatar>
    <Avatar><AvatarFallback style={{ background: 'var(--color-tone-8)', color: 'var(--color-tone-8-fg)' }}>GM</AvatarFallback></Avatar>
    <Avatar><AvatarFallback>??</AvatarFallback></Avatar>
  </div>
);

export const Sizes = () => (
  <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
    <Avatar style={{ width: 28, height: 28 }}><AvatarFallback style={{ fontSize: 12 }}>S</AvatarFallback></Avatar>
    <Avatar><AvatarFallback>M</AvatarFallback></Avatar>
    <Avatar style={{ width: 48, height: 48 }}><AvatarFallback>L</AvatarFallback></Avatar>
  </div>
);
