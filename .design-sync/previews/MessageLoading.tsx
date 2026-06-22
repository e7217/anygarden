// Authored preview for MessageLoading — the three-dot "typing" indicator.
import {
  MessageLoading, ChatBubble, ChatBubbleAvatar, ChatBubbleMessage,
} from 'anygarden-frontend';

export const Indicator = () => (
  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16, fontSize: 14, color: 'var(--color-foreground-muted)' }}>
    <MessageLoading />
    <span>Agent is thinking…</span>
  </div>
);

export const InBubble = () => (
  <div style={{ maxWidth: 420 }}>
    <ChatBubble variant="received">
      <ChatBubbleAvatar fallback="GM" />
      <ChatBubbleMessage isLoading />
    </ChatBubble>
  </div>
);
