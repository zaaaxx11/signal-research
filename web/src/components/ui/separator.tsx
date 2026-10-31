/*
 * shadcn/ui — separator
 *
 * Source:  https://github.com/shadcn-ui/ui/blob/b8d7baa15b1dca5716f13153e63795fc3d741f6b/apps/v4/registry/new-york-v4/ui/separator.tsx
 * Commit:  b8d7baa15b1dca5716f13153e63795fc3d741f6b
 * Licence: MIT (shadcn-ui/ui, LICENSE.md at the same commit)
 * Fetched: 2026-10-30 by scripts/vendor-shadcn.mjs
 *
 * Copied verbatim except two module specifiers (the registry's own install
 * transform): "cn" -> "@/lib/utils" and "@/registry/new-york-v4/ui/*" ->
 * "@/components/ui/*". Do not edit by hand: re-vendor instead, so the commit
 * in this header keeps meaning something.
 */
import * as React from "react"
import { cn } from "@/lib/utils"
import { Separator as SeparatorPrimitive } from "radix-ui"

function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      data-slot="separator"
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 bg-border data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-full data-[orientation=vertical]:h-full data-[orientation=vertical]:w-px",
        className
      )}
      {...props}
    />
  )
}

export { Separator }
