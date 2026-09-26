import * as React from "react"
import { cn } from "@/lib/utils"

const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex min-h-11 w-full rounded-[var(--radius-sm)] border border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)] px-3 py-2 text-base text-[var(--color-foreground)] transition-[border-color,box-shadow] file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-[var(--color-foreground)] placeholder:text-[var(--color-foreground-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] focus-visible:border-[var(--color-brand-focus)] disabled:cursor-not-allowed disabled:opacity-50 md:min-h-9 md:text-sm",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
