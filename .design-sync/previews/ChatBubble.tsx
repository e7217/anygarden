// Authored preview for ChatBubble — received/sent (parent injects variant into children).
import {
  ChatBubble, ChatBubbleAvatar, ChatBubbleMessage, ChatBubbleTimestamp,
} from 'anygarden-frontend';

export const Conversation = () => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 480 }}>
    <ChatBubble variant="received">
      <ChatBubbleAvatar fallback="OR" />
      <ChatBubbleMessage>
        Deploy to staging is queued. I'll hand off to the reviewer once tests pass.
        <ChatBubbleTimestamp timestamp="10:24" />
      </ChatBubbleMessage>
    </ChatBubble>
    <ChatBubble variant="sent">
      <ChatBubbleAvatar fallback="ME" />
      <ChatBubbleMessage>
        Sounds good — ping me if the migration step needs approval.
        <ChatBubbleTimestamp timestamp="10:25" />
      </ChatBubbleMessage>
    </ChatBubble>
  </div>
);

export const Loading = () => (
  <div style={{ maxWidth: 480 }}>
    <ChatBubble variant="received">
      <ChatBubbleAvatar fallback="GM" />
      <ChatBubbleMessage isLoading />
    </ChatBubble>
  </div>
);

// layout="ai" renders a full-width, borderless, top-bordered message — the
// assistant/transcript style. ChatBubble injects layout into its children.
export const AiMessage = () => (
  <div style={{ maxWidth: 560 }}>
    <ChatBubble variant="received" layout="ai">
      <ChatBubbleAvatar fallback="AI" />
      <ChatBubbleMessage>
        I've queued the staging deploy and will hand off to the reviewer once the test suite passes.
        Plan: run migrations, deploy, then smoke-test the room API.
      </ChatBubbleMessage>
    </ChatBubble>
  </div>
);
