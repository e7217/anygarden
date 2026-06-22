// Authored preview for Input — text field states.
import { Input, Label } from 'anygarden-frontend';

export const Default = () => (
  <div style={{ maxWidth: 280 }}><Input placeholder="Search rooms…" /></div>
);

export const WithLabel = () => (
  <div style={{ maxWidth: 280, display: 'flex', flexDirection: 'column', gap: 6 }}>
    <Label htmlFor="rn">Room name</Label>
    <Input id="rn" defaultValue="production-cluster" />
  </div>
);

export const States = () => (
  <div style={{ maxWidth: 280, display: 'flex', flexDirection: 'column', gap: 10 }}>
    <Input placeholder="Enabled" />
    <Input defaultValue="Disabled" disabled />
  </div>
);
