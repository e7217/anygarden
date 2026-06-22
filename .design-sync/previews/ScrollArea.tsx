// Authored preview for ScrollArea — scrollable room list.
import { ScrollArea } from 'anygarden-frontend';

const rooms = [
  'production-cluster', 'staging', 'codex-dm', 'gemini-dm',
  'reviewer-room', 'builder-room', 'sandbox', 'archive', 'incident-2026-04',
];

export const RoomList = () => (
  <ScrollArea
    style={{
      height: 168, width: 260,
      border: '1px solid var(--color-border)',
      borderRadius: 'var(--radius-lg)',
      padding: 12,
    }}
  >
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14 }}>
      {rooms.map((n) => (
        <div key={n} style={{ color: 'var(--color-foreground)' }}>#{n}</div>
      ))}
    </div>
  </ScrollArea>
);
