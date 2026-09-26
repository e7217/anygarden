import * as React from 'react'
import { cn } from '@/lib/utils'

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'min-h-24 w-full min-w-0 resize-y rounded-[var(--radius-sm)] border border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)] px-3 py-2 text-base text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm',
        className,
      )}
      {...props}
    />
  ),
)
Textarea.displayName = 'Textarea'
