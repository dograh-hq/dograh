'use client';

import { Bot, Clock, CreditCard, Megaphone, PhoneCall, ShieldCheck } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';

import { client } from '@/client/client.gen';
import { useAuth } from '@/lib/auth';

type Counts = { total?: number; active?: number; archived?: number };
type Campaign = { state?: string | null };
type DailyItem = { date: string; minutes?: number; call_count?: number };
type Breakdown = { breakdown?: DailyItem[]; total_minutes?: number };
type Balance = { balance_seconds: number | null; unlimited: boolean; plan?: string };
type Kyc = { is_complete?: boolean; kyc_status?: string | null };
type Run = {
  workflow_name: string | null;
  created_at: string;
  call_duration_seconds: number;
  disposition?: string | null;
  phone_number?: string | null;
};

type Metrics = {
  campaignsTotal: number;
  campaignsActive: number;
  agentsTotal: number;
  agentsActive: number;
  calls7d: number;
  minutes7d: number;
  balanceSeconds: number | null;
  unlimited: boolean;
  plan: string;
  kycComplete: boolean;
  kycStatus: string;
  recent: Run[];
};

const ACTIVE_CAMPAIGN_STATES = new Set(['running', 'active', 'in_progress', 'started']);

async function get<T>(url: string): Promise<T | null> {
  try {
    const res = await client.get({ url });
    return (res.data as T) ?? null;
  } catch {
    return null;
  }
}

function timeAgo(iso: string): string {
  const d = new Date(iso).getTime();
  if (Number.isNaN(d)) return '';
  const s = Math.max(0, Math.floor((Date.now() - d) / 1000));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtDur(sec: number): string {
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function planLabel(plan: string): string {
  if (!plan || plan === 'trial') return 'Trial';
  return plan.charAt(0).toUpperCase() + plan.slice(1);
}

export function HomeMetrics() {
  const { user, loading: authLoading } = useAuth();
  const [m, setM] = useState<Metrics | null>(null);
  const fetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || fetched.current) return;
    fetched.current = true;

    (async () => {
      const [counts, campaigns, breakdown, balance, kyc, runs] = await Promise.all([
        get<Counts>('/api/v1/workflow/count'),
        get<{ campaigns?: Campaign[] }>('/api/v1/campaign/'),
        get<Breakdown>('/api/v1/organizations/usage/daily-breakdown'),
        get<Balance>('/api/v1/billing/balance'),
        get<Kyc>('/api/v1/kyc/status'),
        get<{ runs?: Run[] }>('/api/v1/organizations/usage/runs?page=1&limit=5'),
      ]);

      const camps = campaigns?.campaigns ?? [];
      const days = breakdown?.breakdown ?? [];
      setM({
        campaignsTotal: camps.length,
        campaignsActive: camps.filter(
          (c) => c.state && ACTIVE_CAMPAIGN_STATES.has(String(c.state).toLowerCase()),
        ).length,
        agentsTotal: counts?.total ?? 0,
        agentsActive: counts?.active ?? 0,
        calls7d: days.reduce((sum, d) => sum + (d.call_count ?? 0), 0),
        minutes7d: Math.round(breakdown?.total_minutes ?? 0),
        balanceSeconds: balance ? balance.balance_seconds : null,
        unlimited: balance?.unlimited ?? false,
        plan: balance?.plan ?? 'trial',
        kycComplete: !!kyc?.is_complete,
        kycStatus: kyc?.kyc_status ?? 'pending',
        recent: runs?.runs ?? [],
      });
    })();
  }, [authLoading, user]);

  const tiles = [
    {
      icon: Megaphone,
      label: 'Campaigns',
      value: m ? m.campaignsTotal.toLocaleString() : '—',
      sub: m ? `${m.campaignsActive} active` : ' ',
      href: '/campaigns',
    },
    {
      icon: Bot,
      label: 'Voice Agents',
      value: m ? m.agentsTotal.toLocaleString() : '—',
      sub: m ? `${m.agentsActive} active` : ' ',
      href: '/workflow',
    },
    {
      icon: PhoneCall,
      label: 'Calls',
      value: m ? m.calls7d.toLocaleString() : '—',
      sub: 'last 7 days',
      href: '/usage',
    },
    {
      icon: Clock,
      label: 'Minutes Used',
      value: m ? m.minutes7d.toLocaleString() : '—',
      sub: 'last 7 days',
      href: '/reports',
    },
    {
      icon: CreditCard,
      label: 'Credits',
      value: m
        ? m.unlimited
          ? 'Unlimited'
          : Math.floor((m.balanceSeconds ?? 0) / 60).toLocaleString()
        : '—',
      sub: m ? `${planLabel(m.plan)} plan` : ' ',
      href: '/credits',
    },
    {
      icon: ShieldCheck,
      label: 'KYC',
      value: m ? (m.kycComplete ? 'Verified' : 'Pending') : '—',
      sub: m && !m.kycComplete ? 'Finish setup →' : 'Identity verified',
      href: '/kyc',
    },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {tiles.map((t) => (
          <Link
            key={t.label}
            href={t.href}
            className="group flex flex-col rounded-2xl border border-border/60 bg-card p-4 shadow-[var(--shadow-card)] transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-[var(--shadow-pop)]"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-eyebrow text-muted-foreground">{t.label}</span>
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted text-muted-foreground transition-colors duration-200 group-hover:border-primary/30 group-hover:bg-accent group-hover:text-primary">
                <t.icon className="h-3.5 w-3.5" />
              </span>
            </div>
            <p className="metric mt-3 text-h2 leading-none tabular-nums">{t.value}</p>
            <p className="mt-1.5 text-small text-muted-foreground">{t.sub}</p>
          </Link>
        ))}
      </div>

      {m && m.recent.length > 0 && (
        <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)] transition-all duration-200">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-eyebrow text-muted-foreground">Recent calls</span>
            <Link href="/usage" className="text-small text-primary transition-colors hover:underline">
              View all
            </Link>
          </div>
          <ul className="divide-y divide-border/50">
            {m.recent.map((r, i) => (
              <li
                key={i}
                className="-mx-2 flex items-center justify-between gap-3 rounded-lg px-2 py-2.5 transition-colors duration-200 hover:bg-accent/40"
              >
                <div className="min-w-0">
                  <p className="truncate text-label">
                    {r.workflow_name || 'Agent'}
                  </p>
                  <p className="truncate text-small text-muted-foreground">
                    {r.phone_number || r.disposition || 'call'}
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <p className="text-small tabular-nums">{fmtDur(r.call_duration_seconds)}</p>
                  <p className="text-small text-muted-foreground">{timeAgo(r.created_at)}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
