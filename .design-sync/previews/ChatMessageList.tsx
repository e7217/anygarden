// Authored preview for ChatMessageList — scrollable transcript of ChatBubbles.
import {
  ChatMessageList, ChatBubble, ChatBubbleAvatar, ChatBubbleMessage,
} from 'anygarden-frontend';

export const Transcript = () => (
  <div style={{ height: 280, width: 440, border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)' }}>
    <ChatMessageList>
      <ChatBubble variant="received">
        <ChatBubbleAvatar fallback="OR" />
        <ChatBubbleMessage>Starting the staging deploy now.</ChatBubbleMessage>
      </ChatBubble>
      <ChatBubble variant="sent">
        <ChatBubbleAvatar fallback="ME" />
        <ChatBubbleMessage>Thanks — keep me posted.</ChatBubbleMessage>
      </ChatBubble>
      <ChatBubble variant="received">
        <ChatBubbleAvatar fallback="RV" />
        <ChatBubbleMessage>Tests passed. Merging and promoting to production.</ChatBubbleMessage>
      </ChatBubble>
    </ChatMessageList>
  </div>
);
