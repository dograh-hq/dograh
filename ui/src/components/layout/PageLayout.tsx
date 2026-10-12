import type { ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { PageShell, type PageShellWidth } from "./PageShell";

/** Shared layout for top-level pages; editors can use PageShell directly. */
export function PageLayout({ title, description, actions, width, children }: {
    title: ReactNode;
    description?: ReactNode;
    actions?: ReactNode;
    /** Content measure; single-column forms read better narrow. */
    width?: PageShellWidth;
    children: ReactNode;
}) {
    return <PageShell width={width}>
        <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1 basis-full sm:basis-64">
                <h1 className="mb-2 text-3xl font-bold">{title}</h1>
                {description && <p className="text-muted-foreground">{description}</p>}
            </div>
            {actions && <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </div>
        <div className="space-y-6">{children}</div>
    </PageShell>;
}

/** A collection or settings section, with optional actions and search/filter controls. */
export function PageSection({ title, description, actions, toolbar, children }: {
    title: ReactNode;
    description?: ReactNode;
    actions?: ReactNode;
    toolbar?: ReactNode;
    children: ReactNode;
}) {
    return <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-4 space-y-0">
            <div className="min-w-0 space-y-1.5">
                <CardTitle>{title}</CardTitle>
                {description && <CardDescription>{description}</CardDescription>}
            </div>
            {actions && <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </CardHeader>
        <CardContent className="space-y-4">
            {toolbar && <div>{toolbar}</div>}
            {children}
        </CardContent>
    </Card>;
}
