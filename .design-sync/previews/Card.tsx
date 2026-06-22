// Authored preview for Card — compound composition (Header/Title/Description/Content/Footer).
import {
  Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter,
  Button, Badge,
} from 'anygarden-frontend';

export const Basic = () => (
  <div style={{ maxWidth: 380 }}>
    <Card>
      <CardHeader>
        <CardTitle>Production cluster</CardTitle>
        <CardDescription>3 agents · 2 machines online</CardDescription>
      </CardHeader>
      <CardContent>
        <p style={{ margin: 0, fontSize: 14, color: 'var(--color-foreground-muted)' }}>
          A multi-agent room coordinating deploys across the fleet. Last activity 4 minutes ago.
        </p>
      </CardContent>
      <CardFooter style={{ gap: 8 }}>
        <Button size="sm">Open room</Button>
        <Button size="sm" variant="ghost">Settings</Button>
      </CardFooter>
    </Card>
  </div>
);

export const WithBadge = () => (
  <div style={{ maxWidth: 380 }}>
    <Card>
      <CardHeader>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <CardTitle>codex-dm</CardTitle>
          <Badge>online</Badge>
        </div>
        <CardDescription>Direct message · Codex engine</CardDescription>
      </CardHeader>
      <CardContent>
        <p style={{ margin: 0, fontSize: 14, color: 'var(--color-foreground-muted)' }}>
          Private channel with the Codex agent. Tasks created here run on its assigned machine.
        </p>
      </CardContent>
    </Card>
  </div>
);
