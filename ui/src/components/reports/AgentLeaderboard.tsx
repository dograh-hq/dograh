'use client';

import { Trophy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { useAuth } from '@/lib/auth';

type Agent = {
  workflow_id: number;
  workflow_name: string;
  total_runs: number;
  total_minutes: number;
  avg_duration_seconds: number;
  transfer_rate_percent: number;
  last_run_at: string | null;
};

function fmtDur(sec: number): string {
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return '—';
  const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

const RANGES = [
  { label: '7 days', value: 7 },
  { label: '30 days', value: 30 },
];

export function AgentLeaderboard() {
  const { user, loading: authLoading } = useAuth();
  const [days, setDays] = useState(7);
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const ready = useRef(false);

  useEffect(() => {
    if (authLoading || !user) return;
    ready.current = true;
    let active = true;
    setLoading(true);
    (async () => {
      try {
        const res = await client.get({
          url: `/api/v1/organizations/reports/agent-leaderboard?days=${days}`,
        });
        const data = res.data as { agents?: Agent[] } | undefined;
        if (active) setAgents(data?.agents ?? []);
      } catch {
        if (active) setAgents([]);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [authLoading, user, days]);

  const maxRuns = agents && agents.length ? Math.max(...agents.map((a) => a.total_runs)) : 1;

  return (
    <div className="rounded-xl border bg-card p-5 shadow-[var(--shadow-card)]">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Trophy className="h-4 w-4 text-primary" />
          <h3 className="text-h3">Agent performance</h3>
        </div>
        <div className="flex rounded-lg border p-0.5">
          {RANGES.map((r) => (
            <button
              key={r.value}
              onClick={() => setDays(r.value)}
              className={`rounded-md px-2.5 py-1 text-small transition-colors ${
                days === r.value
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Loading…</p>
      ) : !agents || agents.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No calls in the last {days} days yet.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-eyebrow text-muted-foreground">
                <th className="pb-2 text-left font-semibold">Agent</th>
                <th className="pb-2 text-right font-semibold">Calls</th>
                <th className="pb-2 text-right font-semibold">Avg</th>
                <th className="hidden pb-2 text-right font-semibold sm:table-cell">Minutes</th>
                <th className="hidden pb-2 text-right font-semibold sm:table-cell">Transfer</th>
                <th className="hidden pb-2 text-right font-semibold md:table-cell">Last run</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {agents.map((a) => (
                <tr key={a.workflow_id} className="group">
                  <td className="py-2.5 pr-3">
                    <div className="truncate text-label">{a.workflow_name}</div>
                    <div className="mt-1 h-1 w-full max-w-[160px] overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary/70"
                        style={{ width: `${Math.max(4, (a.total_runs / maxRuns) * 100)}%` }}
                      />
                    </div>
                  </td>
                  <td className="py-2.5 text-right tabular font-medium">
                    {a.total_runs.toLocaleString()}
                  </td>
                  <td className="py-2.5 text-right tabular text-muted-foreground">
                    {fmtDur(a.avg_duration_seconds)}
                  </td>
                  <td className="hidden py-2.5 text-right tabular text-muted-foreground sm:table-cell">
                    {a.total_minutes.toLocaleString()}
                  </td>
                  <td className="hidden py-2.5 text-right tabular sm:table-cell">
                    <span
                      className={
                        a.transfer_rate_percent > 0 ? 'text-[var(--success)]' : 'text-muted-foreground'
                      }
                    >
                      {a.transfer_rate_percent}%
                    </span>
                  </td>
                  <td className="hidden py-2.5 text-right text-muted-foreground md:table-cell">
                    {timeAgo(a.last_run_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
