// Authored preview for ChatInput — auto-sizing message textarea.
import { ChatInput } from 'anygarden-frontend';

export const Empty = () => (
  <div style={{ maxWidth: 440 }}><ChatInput placeholder="Message the room…" /></div>
);

export const WithText = () => (
  <div style={{ maxWidth: 440 }}>
    <ChatInput defaultValue="@orchestrator can you retry the failed staging deploy?" />
  </div>
);
