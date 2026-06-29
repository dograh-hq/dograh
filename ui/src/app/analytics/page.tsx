'use client';

import { Suspense } from 'react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { ReportsView } from '../reports/ReportsView';
import { RunsView } from '../usage/RunsView';

export default function AnalyticsPage() {
  return (
    <div className="container mx-auto space-y-8 p-6">
      {/* Premium header */}
      <div>
        <p className="text-eyebrow text-primary">Observe</p>
        <h1 className="text-h1 mt-1">Analytics</h1>
        <p className="text-body mt-1 text-muted-foreground">
          Performance insights and a complete history of every agent run, in one place.
        </p>
      </div>

      <Suspense fallback={null}>
        <Tabs defaultValue="overview" className="gap-6">
          <TabsList className="h-11 rounded-2xl border border-border/60 bg-card p-1 shadow-[var(--shadow-card)]">
            <TabsTrigger
              value="overview"
              className="rounded-xl px-5 text-sm font-medium text-muted-foreground transition-all duration-200 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-[var(--shadow-pop)]"
            >
              Overview
            </TabsTrigger>
            <TabsTrigger
              value="runs"
              className="rounded-xl px-5 text-sm font-medium text-muted-foreground transition-all duration-200 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-[var(--shadow-pop)]"
            >
              Runs
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="mt-0">
            <ReportsView showHeader={false} />
          </TabsContent>

          <TabsContent value="runs" className="mt-0">
            <RunsView showHeader={false} />
          </TabsContent>
        </Tabs>
      </Suspense>
    </div>
  );
}
