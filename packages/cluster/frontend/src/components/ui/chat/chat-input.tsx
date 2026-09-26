import * as React from "react";
import { cn } from "@/lib/utils";

interface ChatInputProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {}

const ChatInput = React.forwardRef<HTMLTextAreaElement, ChatInputProps>(
  ({ className, ...props }, ref) => (
    <textarea
      autoComplete="off"
      ref={ref}
      name="message"
      className={cn(
        "flex min-h-11 max-h-40 w-full resize-none rounded-[var(--radius-md)] border border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)] px-4 py-3 text-base text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className,
      )}
      {...props}
    />
  ),
);
ChatInput.displayName = "ChatInput";

export { ChatInput };
