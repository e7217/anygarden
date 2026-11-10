import * as React from "react"
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area"
import { cn } from "@/lib/utils"

const ScrollArea = React.forwardRef<
  React.ComponentRef<typeof ScrollAreaPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root>
>(({ className, children, ...props }, ref) => (
  <ScrollAreaPrimitive.Root
    ref={ref}
    className={cn("relative overflow-hidden", className)}
    {...props}
  >
    {/*
      #336 — Radix injects ``display: table; min-width: 100%`` as inline
      style on the viewport's first child. ``display: table`` sizes that
      wrapper to descendants' min-content width, which for ``truncate``
      (white-space: nowrap) is the full unbroken text width — defeating
      ``min-w-0`` / ``flex-1`` / ``truncate`` further down the tree and
      letting any long row push the rail's column past its rail width.
      Forcing the wrapper to ``display: block`` while preserving its
      ``min-width: 100%`` keeps the layout substrate honest so every
      ScrollArea consumer (right rail, sidebar, chat area) gets correct
      truncation for free.
    */}
    <ScrollAreaPrimitive.Viewport
      className="h-full w-full rounded-[inherit] [&>div]:!block [&>div]:!min-w-full"
    >
      {children}
    </ScrollAreaPrimitive.Viewport>
    <ScrollBar />
    <ScrollAreaPrimitive.Corner />
  </ScrollAreaPrimitive.Root>
))
ScrollArea.displayName = ScrollAreaPrimitive.Root.displayName

const ScrollBar = React.forwardRef<
  React.ComponentRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(({ className, orientation = "vertical", ...props }, ref) => (
  <ScrollAreaPrimitive.ScrollAreaScrollbar
    ref={ref}
    orientation={orientation}
    className={cn(
      "flex touch-none select-none transition-colors",
      orientation === "vertical" && "h-full w-2 border-l border-l-transparent p-px",
      orientation === "horizontal" && "h-2 flex-col border-t border-t-transparent p-px",
      className
    )}
    {...props}
  >
    <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-[var(--radius-pill)] bg-[var(--color-border-strong)]" />
  </ScrollAreaPrimitive.ScrollAreaScrollbar>
))
ScrollBar.displayName = ScrollAreaPrimitive.ScrollAreaScrollbar.displayName

/**
 * #1013 — scrolls the ScrollArea that holds ``anchor`` to its end.
 *
 * ``anchor.scrollIntoView()`` aligns to the viewport's top by default;
 * when the viewport alone cannot do that it scrolls every ancestor too,
 * including the ``overflow-hidden`` root above, which then slides the
 * whole viewport up and leaves the panel half blank. Scrolling the
 * viewport itself never touches an ancestor.
 */
function scrollToEnd(anchor: HTMLElement | null, behavior: ScrollBehavior = "smooth") {
  if (!anchor) return
  const viewport = anchor.closest<HTMLElement>("[data-radix-scroll-area-viewport]")
  if (!viewport) {
    anchor.scrollIntoView?.({ behavior, block: "end" })
    return
  }
  if (typeof viewport.scrollTo === "function") viewport.scrollTo({ top: viewport.scrollHeight, behavior })
  else viewport.scrollTop = viewport.scrollHeight
}

export { ScrollArea, ScrollBar, scrollToEnd }
