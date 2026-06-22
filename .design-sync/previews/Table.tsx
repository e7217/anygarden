// Authored preview for Table — text-heavy compound (Header/Body/Row/Head/Cell).
import {
  Table, TableHeader, TableBody, TableFooter, TableRow, TableHead, TableCell, TableCaption,
  Badge,
} from 'anygarden-frontend';

export const AgentRoster = () => (
  <div style={{ maxWidth: 560 }}>
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Agent</TableHead>
          <TableHead>Engine</TableHead>
          <TableHead>Status</TableHead>
          <TableHead style={{ textAlign: 'right' }}>Turns</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow data-state="selected">
          <TableCell style={{ fontWeight: 500 }}>orchestrator</TableCell>
          <TableCell>Claude</TableCell>
          <TableCell><Badge>online</Badge></TableCell>
          <TableCell style={{ textAlign: 'right' }}>128</TableCell>
        </TableRow>
        <TableRow>
          <TableCell style={{ fontWeight: 500 }}>reviewer</TableCell>
          <TableCell>Codex</TableCell>
          <TableCell><Badge variant="secondary">idle</Badge></TableCell>
          <TableCell style={{ textAlign: 'right' }}>42</TableCell>
        </TableRow>
        <TableRow>
          <TableCell style={{ fontWeight: 500 }}>builder</TableCell>
          <TableCell>Gemini</TableCell>
          <TableCell><Badge variant="destructive">error</Badge></TableCell>
          <TableCell style={{ textAlign: 'right' }}>7</TableCell>
        </TableRow>
      </TableBody>
      <TableFooter>
        <TableRow>
          <TableCell style={{ fontWeight: 600 }}>Total</TableCell>
          <TableCell />
          <TableCell />
          <TableCell style={{ textAlign: 'right', fontWeight: 600 }}>177</TableCell>
        </TableRow>
      </TableFooter>
      <TableCaption>Agents currently joined to the production room (selected row highlighted).</TableCaption>
    </Table>
  </div>
);
