// Authored preview for Badge — status pill variants.
import { Badge } from 'anygarden-frontend';

const row: React.CSSProperties = { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' };

export const Variants = () => (
  <div style={row}>
    <Badge>online</Badge>
    <Badge variant="secondary">idle</Badge>
    <Badge variant="destructive">error</Badge>
    <Badge variant="outline">draft</Badge>
  </div>
);

export const InContext = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 14 }}>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <span>production-cluster</span><Badge>3 agents</Badge>
    </div>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <span>staging</span><Badge variant="secondary">paused</Badge>
    </div>
  </div>
);
