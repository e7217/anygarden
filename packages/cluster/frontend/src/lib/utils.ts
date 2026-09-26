import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

// Our named type scale is implemented with @utility in index.css. Without
// registering it, tailwind-merge treats text-heading/lead/etc. as colours
// and silently removes them when a component also sets its text colour.
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['display', 'title', 'heading', 'lead', 'caption', 'badge'] }],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return merge(clsx(inputs))
}
