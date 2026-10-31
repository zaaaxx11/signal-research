/*
 * shadcn/ui — resizable
 *
 * Source:  https://github.com/shadcn-ui/ui/blob/b8d7baa15b1dca5716f13153e63795fc3d741f6b/apps/v4/registry/new-york-v4/ui/resizable.tsx
 * Commit:  b8d7baa15b1dca5716f13153e63795fc3d741f6b
 * Licence: MIT (shadcn-ui/ui, LICENSE.md at the same commit)
 * Fetched: 2026-10-30 by scripts/vendor-shadcn.mjs
 *
 * Copied verbatim except two module specifiers (the registry's own install
 * transform): "cn" -> "@/lib/utils" and "@/registry/new-york-v4/ui/*" ->
 * "@/components/ui/*". Do not edit by hand: re-vendor instead, so the commit
 * in this header keeps meaning something.
 */
import { cn } from "@/lib/utils"
import { GripVerticalIcon } from "lucide-react"
import * as ResizablePrimitive from "react-resizable-panels"

function ResizablePanelGroup({
  className,
  ...props
}: ResizablePrimitive.GroupProps) {
  return (
    <ResizablePrimitive.Group
      data-slot="resizable-panel-group"
      className={cn(
        "flex h-full w-full aria-[orientation=vertical]:flex-col",
        className
      )}
      {...props}
    />
  )
}

function ResizablePanel({ ...props }: ResizablePrimitive.PanelProps) {
  return <ResizablePrimitive.Panel data-slot="resizable-panel" {...props} />
}

function ResizableHandle({
  withHandle,
  className,
  ...props
}: ResizablePrimitive.SeparatorProps & {
  withHandle?: boolean
}) {
  return (
    <ResizablePrimitive.Separator
      data-slot="resizable-handle"
      className={cn(
        "relative flex w-px items-center justify-center bg-border after:absolute after:inset-y-0 after:left-1/2 after:w-1 after:-translate-x-1/2 focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:outline-hidden aria-[orientation=horizontal]:h-px aria-[orientation=horizontal]:w-full aria-[orientation=horizontal]:after:left-0 aria-[orientation=horizontal]:after:h-1 aria-[orientation=horizontal]:after:w-full aria-[orientation=horizontal]:after:translate-x-0 aria-[orientation=horizontal]:after:-translate-y-1/2 [&[aria-orientation=horizontal]>div]:rotate-90",
        className
      )}
      {...props}
    >
      {withHandle && (
        <div className="z-10 flex h-4 w-3 items-center justify-center rounded-xs border bg-border">
          <GripVerticalIcon className="size-2.5" />
        </div>
      )}
    </ResizablePrimitive.Separator>
  )
}

export { ResizableHandle, ResizablePanel, ResizablePanelGroup }
