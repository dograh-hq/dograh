"use client";

import { ArrowLeft, BookOpen } from "lucide-react";
import Link from "next/link";

export default function DocsComingSoonPage() {
  return (
    <div className="flex min-h-[80vh] items-center justify-center px-4">
      <div className="max-w-md text-center">
        <div className="mx-auto mb-6 flex h-12 w-12 items-center justify-center rounded-full border border-border/60 bg-muted/30">
          <BookOpen className="h-6 w-6 text-muted-foreground" />
        </div>
        <span className="mb-4 inline-block rounded-full border border-border/60 px-3 py-1 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Coming Soon
        </span>
        <h1 className="mb-3 text-3xl font-semibold">Developer Documentation</h1>
        <p className="mb-8 text-muted-foreground">
          We&apos;re preparing comprehensive guides, API references, and tutorials
          to help you build with Vani. Stay tuned.
        </p>
        <Link
          href="/workflow"
          className="inline-flex items-center gap-2 rounded-lg border border-border/60 px-4 py-2.5 text-sm font-medium transition-colors hover:bg-accent"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Studio
        </Link>
      </div>
    </div>
  );
}
