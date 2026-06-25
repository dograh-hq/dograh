'use client';

import { BarChart3 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { useAuth } from '@/lib/auth';

type DailyItem = { date: string; minutes?: number; call_count?: number };

function shortDay(date: string): string {
  const d = new Date(date);
  if (Number.isNaN(d.getTime())) return date.slice(5);
  return d.toLocaleDateString(undefined, { weekday: 'short' });
}

export function CallVolumeChart() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState<DailyItem[] | null>(null);
  const fetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || fetched.current) return;
    fetched.current = true;
    (async () => {
      try {
        const res = await client.get({
          url: '/api/v1/organizations/usage/daily-breakdown',
        });
        const data = res.data as { breakdown?: DailyItem[] } | undefined;
        setDays(data?.breakdown ?? []);
      } catch {
        setDays([]);
      }
    })();
  }, [authLoading, user]);

  const items = (days ?? []).slice(-14);
  const max = items.length ? Math.max(1, ...items.map((d) => d.call_count ?? 0)) : 1;
  const totalCalls = items.reduce((s, d) => s + (d.call_count ?? 0), 0);

  return (
    <div className="rounded-xl border bg-card p-5 shadow-[var(--shadow-card)]">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-primary" />
          <h3 className="text-h3">Call volume</h3>
        </div>
        <span className="text-small text-muted-foreground">
          {totalCalls.toLocaleString()} calls · {items.length}d
        </span>
      </div>

      {!days ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">No call activity yet.</p>
      ) : (
        <div className="flex h-40 items-end gap-1.5">
          {items.map((d, i) => {
            const count = d.call_count ?? 0;
            const h = Math.max(2, (count / max) * 100);
            return (
              <div key={i} className="group flex flex-1 flex-col items-center justify-end gap-1.5">
                <div className="relative w-full">
                  <span className="pointer-events-none absolute -top-5 left-1/2 -translate-x-1/2 text-small tabular opacity-0 transition-opacity group-hover:opacity-100">
                    {count}
                  </span>
                  <div
                    className="w-full rounded-t-md bg-primary/75 transition-all group-hover:bg-primary"
                    style={{ height: `${h * 1.2}px` }}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground">{shortDay(d.date)}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
