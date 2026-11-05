import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Standard content column for every route rendered inside AppLayout.
 *
 * AppLayout's <main> is intentionally unpadded and unconstrained, so each page
 * needs its own wrapper to get gutters and a measure. Routing that through one
 * component is what keeps those values from drifting apart per page.
 *
 * Tailwind's `container` is deliberately NOT used here. Its max-widths are keyed
 * to VIEWPORT breakpoints, but this element lives inside the sidebar inset
 * (viewport - 16rem), so the cap only ever binds above a ~1792px viewport and is
 * a no-op below that. Pages that leaned on it were effectively uncapped, which
 * is how their widths silently diverged.
 */
const WIDTHS = {
    /** Lists, dashboards, detail views — the measure to reach for. */
    default: "max-w-7xl",
    /** Single-column forms, where a full measure would strand the labels. */
    narrow: "max-w-2xl",
    /** Wide tables that need every available pixel. */
    full: "max-w-none",
} as const;

export type PageShellWidth = keyof typeof WIDTHS;

interface PageShellProps {
    children: ReactNode;
    width?: PageShellWidth;
    className?: string;
}

export function PageShell({ children, width = "default", className }: PageShellProps) {
    return (
        <div className={cn("mx-auto w-full px-4 py-8", WIDTHS[width], className)}>
            {children}
        </div>
    );
}
