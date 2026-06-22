// Authored preview for Label — paired with Input (the only true render).
import { Label, Input } from 'anygarden-frontend';

export const WithInput = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 280 }}>
    <Label htmlFor="email">Email address</Label>
    <Input id="email" type="email" placeholder="you@anygarden.dev" />
  </div>
);

export const Standalone = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
    <Label>Engine</Label>
    <Label>Machine</Label>
    <Label>Owner</Label>
  </div>
);
