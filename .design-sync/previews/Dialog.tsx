// Authored preview for Dialog — rendered in its open state (controlled `open`).
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter,
  Button,
} from 'anygarden-frontend';

export const Open = () => (
  <Dialog open>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Delete room?</DialogTitle>
        <DialogDescription>
          This permanently removes “production-cluster” and detaches its agents.
          This action cannot be undone.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter>
        <Button variant="ghost">Cancel</Button>
        <Button variant="destructive">Delete room</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);
