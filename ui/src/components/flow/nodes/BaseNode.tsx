import { forwardRef, HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export const BaseNode = forwardRef<
    HTMLDivElement,
    HTMLAttributes<HTMLDivElement> & {
        selected?: boolean;
        invalid?: boolean;
        selected_through_edge?: boolean;
        hovered_through_edge?: boolean;
    }
>(({ className, selected, invalid, selected_through_edge, hovered_through_edge, ...props }, ref) => (
    <div
        ref={ref}
        className={cn(
            "relative rounded-md border bg-card p-5 text-card-foreground min-w-[300px] min-h-[100px]",
            className,
            selected ? "border-muted-foreground shadow-lg" : "",
            invalid ? "border-red-500 shadow-[0_0_10px_rgba(239,68,68,0.5)]" : "",
            // Hovered through edge takes precedence over selected through edge
            hovered_through_edge ? "ring-2 ring-blue-400 shadow-[0_0_12px_rgba(96,165,250,0.5)]" : "",
            !hovered_through_edge && selected_through_edge ? "ring-1 ring-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.4)]" : "",
            !selected_through_edge && !hovered_through_edge && "hover:ring-1 hover:ring-gray-300",
        )}
        tabIndex={0}
        {...props}
    />
));

BaseNode.displayName = "BaseNode";
