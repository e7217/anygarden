import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center rounded-[var(--radius-pill)] px-2 py-0.5 text-badge transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-brand-focus)] focus:ring-offset-1",
  {
    variants: {
      variant: {
        default:
          "bg-[var(--color-brand-tint-bg)] text-[var(--color-brand-tint-text)]",
        secondary:
          "bg-[var(--color-surface-alt)] text-[var(--color-foreground-muted)]",
        destructive:
          "bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]",
        outline:
          "border border-[var(--color-border)] text-[var(--color-foreground)]",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
