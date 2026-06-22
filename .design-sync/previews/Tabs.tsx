// Authored preview for Tabs — segmented control + active panel.
import { Tabs, TabsList, TabsTrigger, TabsContent } from 'anygarden-frontend';

const panel: React.CSSProperties = { fontSize: 14, color: 'var(--color-foreground-muted)', margin: 0 };

export const Default = () => (
  <div style={{ maxWidth: 420 }}>
    <Tabs defaultValue="agents">
      <TabsList>
        <TabsTrigger value="agents">Agents</TabsTrigger>
        <TabsTrigger value="machines">Machines</TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      <TabsContent value="agents"><p style={panel}>3 agents joined: orchestrator, reviewer, builder.</p></TabsContent>
      <TabsContent value="machines"><p style={panel}>2 machines online.</p></TabsContent>
      <TabsContent value="activity"><p style={panel}>Last handoff 4 minutes ago.</p></TabsContent>
    </Tabs>
  </div>
);
