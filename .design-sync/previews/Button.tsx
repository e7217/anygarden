// Authored preview for Button — anygarden DS primitive.
import { Button } from 'anygarden-frontend';

const row: React.CSSProperties = { display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' };

export const Variants = () => (
  <div style={row}>
    <Button>Create room</Button>
    <Button variant="secondary">Invite</Button>
    <Button variant="outline">Settings</Button>
    <Button variant="ghost">Cancel</Button>
    <Button variant="destructive">Delete room</Button>
    <Button variant="link">View activity</Button>
  </div>
);

export const Sizes = () => (
  <div style={row}>
    <Button size="sm">Small</Button>
    <Button size="default">Default</Button>
    <Button size="lg">Large</Button>
  </div>
);

export const States = () => (
  <div style={row}>
    <Button>Enabled</Button>
    <Button disabled>Disabled</Button>
    <Button variant="outline" disabled>Outline disabled</Button>
  </div>
);
