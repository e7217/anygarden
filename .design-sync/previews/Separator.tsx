// Authored preview for Separator — horizontal + vertical.
import { Separator } from 'anygarden-frontend';

export const Horizontal = () => (
  <div style={{ maxWidth: 320 }}>
    <div style={{ fontSize: 14, fontWeight: 600 }}>Room settings</div>
    <Separator style={{ margin: '12px 0' }} />
    <div style={{ fontSize: 14, color: 'var(--color-foreground-muted)' }}>
      Manage agents, machines, and access
    </div>
  </div>
);

export const Vertical = () => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, height: 20, fontSize: 14 }}>
    <span>Docs</span>
    <Separator orientation="vertical" />
    <span>API</span>
    <Separator orientation="vertical" />
    <span>Settings</span>
  </div>
);
