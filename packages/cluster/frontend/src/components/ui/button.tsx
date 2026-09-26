import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-sm)] text-sm font-medium transition-[background-color,color,box-shadow] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-brand-focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-background)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-[var(--color-brand)] text-[var(--color-on-brand)] hover:bg-[var(--color-brand-hover)]",
        destructive:
          "bg-[var(--color-danger-solid)] text-[var(--color-destructive-foreground)] hover:bg-[var(--color-danger-solid-hover)]",
        outline:
          "border border-[var(--color-border-strong)] bg-[var(--color-surface-elevated)] text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)]",
        secondary:
          "bg-[var(--color-surface-alt)] text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)]",
        ghost:
          "bg-transparent text-[var(--color-foreground)] hover:bg-[var(--color-surface-hover)] cursor-pointer",
        link:
          "text-[var(--color-link)] underline-offset-4 hover:underline",
      },
      size: {
        default: "min-h-11 px-4 py-2 md:min-h-9",
        sm: "min-h-11 px-3 py-1.5 md:min-h-8",
        lg: "min-h-11 px-5 py-2.5 text-base md:min-h-10",
        icon: "size-11 md:size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
