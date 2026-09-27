'use client';

import { Download, Minus, TrendingDown, TrendingUp } from 'lucide-react';

import { Button } from '@/components/ui/button';

export type CalmTurn = {
    role?: string;
    turn_id?: number | string;
    text?: string;
    utterance_verbatim?: string;
    scores?: Record<string, number | null>;
    calm_scores?: Record<string, number | { score?: number | null; confidence?: number | null } | null>;
    emotional_scores?: Record<string, number | null>;
    safety_scores?: Record<string, number | null>;
    trend?: Record<string, unknown>;
    prompt_sent_to_llm?: string;
};

type Trend = { direction?: string; delta_previous?: number | null };

function valuesFor(turn: CalmTurn, kind: 'emotional' | 'safety') {
    if (kind === 'safety') return turn.safety_scores ?? {};
    const values = turn.emotional_scores ?? turn.scores ?? turn.calm_scores ?? {};
    return Object.fromEntries(Object.entries(values).map(([name, value]) => [name, typeof value === 'number' ? value : value?.score ?? null]));
}

function displayTrend(rawTrend?: unknown) {
    const trend = rawTrend as Trend | undefined;
    if (!trend || trend.direction === 'insufficient_data' || trend.delta_previous == null) {
        return { icon: <Minus className="h-3.5 w-3.5 text-muted-foreground" />, label: '—' };
    }
    if (trend.direction === 'unchanged' || trend.delta_previous === 0) {
        return { icon: <Minus className="h-3.5 w-3.5 text-muted-foreground" />, label: '=' };
    }
    const positive = trend.delta_previous > 0;
    return {
        icon: positive ? <TrendingUp className="h-3.5 w-3.5 text-emerald-600" /> : <TrendingDown className="h-3.5 w-3.5 text-rose-600" />,
        label: `${positive ? '+' : ''}${trend.delta_previous.toFixed(1)}`,
    };
}

function ScoreChart({ title, turns, kind }: { title: string; turns: CalmTurn[]; kind: 'emotional' | 'safety' }) {
    const series = Array.from(new Set(turns.flatMap((turn) => Object.keys(valuesFor(turn, kind)))));
    if (!turns.length || !series.length) return null;

    const width = 560;
    const height = 210;
    const left = 34;
    const right = 12;
    const top = 18;
    const bottom = 30;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const x = (index: number) => left + (turns.length === 1 ? plotWidth / 2 : (index * plotWidth) / (turns.length - 1));
    const y = (score: number | null | undefined) => top + plotHeight - ((Math.max(0, Math.min(10, score ?? 0)) / 10) * plotHeight);
    const colors = ['#2563eb', '#db2777', '#16a34a', '#ea580c', '#7c3aed', '#0891b2'];

    return (
        <div className="rounded-lg border bg-background/60 p-3">
            <p className="mb-2 text-sm font-semibold">{title}</p>
            <div className="overflow-x-auto">
                <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[520px]" role="img" aria-label={`${title} score trajectory`}>
                    {[0, 5, 10].map((tick) => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="currentColor" className="text-border" strokeDasharray="3 3" /><text x={left - 7} y={y(tick) + 4} textAnchor="end" fontSize="10" fill="currentColor" className="text-muted-foreground">{tick}</text></g>)}
                    {turns.map((turn, index) => <text key={`${String(turn.turn_id)}-${index}`} x={x(index)} y={height - 10} textAnchor="middle" fontSize="10" fill="currentColor" className="text-muted-foreground">{String(turn.turn_id ?? index + 1)}</text>)}
                    {series.map((name, seriesIndex) => <polyline key={name} points={turns.map((turn, index) => `${x(index)},${y(valuesFor(turn, kind)[name])}`).join(' ')} fill="none" stroke={colors[seriesIndex % colors.length]} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />)}
                    {series.map((name, seriesIndex) => turns.map((turn, index) => <circle key={`${name}-${String(turn.turn_id)}-${index}`} cx={x(index)} cy={y(valuesFor(turn, kind)[name])} r="2.8" fill={colors[seriesIndex % colors.length]} />))}
                </svg>
            </div>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">{series.map((name, index) => <span key={name} className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ backgroundColor: colors[index % colors.length] }} />{name.replaceAll('_', ' ')}</span>)}</div>
            <p className="mt-1 text-[11px] text-muted-foreground">Y-axis: score 0–10 · X-axis: turn</p>
        </div>
    );
}

export function TurnByTurnCalmPanel({ turns, title = 'Turn-by-turn CALM scoring' }: { turns: CalmTurn[]; title?: string }) {
    if (!turns.length) return null;

    const download = () => {
        const blob = new Blob([JSON.stringify(turns, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'calm-turn-by-turn.json';
        anchor.click();
        URL.revokeObjectURL(url);
    };
    const columns = Array.from(new Set(turns.flatMap((turn) => Object.keys(valuesFor(turn, 'emotional')))));

    return (
        <section className="space-y-4 rounded-xl border border-border bg-card p-4" aria-label={title}>
            <div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">{title}</h3><p className="text-xs text-muted-foreground">Scores, trend movement, safety/emotional trajectories, and the exact engineered prompt used for each turn.</p></div><Button size="sm" variant="outline" onClick={download}><Download className="mr-2 h-4 w-4" />Download</Button></div>
            <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-sm"><thead><tr className="border-b text-left"><th className="p-2">Role</th><th className="p-2">Turn</th><th className="p-2">Text</th>{columns.map((name) => <th key={name} className="p-2">{name.replaceAll('_', ' ')}</th>)}<th className="p-2">Prompt</th></tr></thead><tbody>{turns.map((turn, index) => { const scores = valuesFor(turn, 'emotional'); return <tr key={`${turn.role ?? 'turn'}-${String(turn.turn_id ?? index)}`} className="border-b last:border-0 align-top"><td className="p-2 font-medium capitalize">{turn.role ?? 'caller'}</td><td className="p-2 font-mono">{turn.turn_id ?? index + 1}</td><td className="max-w-xs p-2 text-muted-foreground">{turn.text ?? turn.utterance_verbatim ?? '—'}</td>{columns.map((name) => { const trend = displayTrend(turn.trend?.[name]); return <td key={name} className="p-2"><span className="inline-flex items-center gap-1 tabular-nums">{scores[name] ?? '—'} {trend.icon}<span className="text-xs text-muted-foreground">{trend.label}</span></span></td>; })}<td className="p-2">{turn.prompt_sent_to_llm ? <details><summary className="cursor-pointer text-xs font-medium text-primary">View engineered prompt</summary><pre className="mt-2 max-h-48 max-w-md overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-xs">{turn.prompt_sent_to_llm}</pre></details> : <span className="text-xs text-muted-foreground">Not available</span>}</td></tr>; })}</tbody></table></div>
            <div className="grid gap-4 xl:grid-cols-2"><ScoreChart title="Caller / service user — emotional scores" turns={turns.filter((turn) => (turn.role ?? 'caller') !== 'sakinah')} kind="emotional" /><ScoreChart title="Caller / service user — safety scores" turns={turns.filter((turn) => (turn.role ?? 'caller') !== 'sakinah')} kind="safety" /><ScoreChart title="Sakinah — emotional scores" turns={turns.filter((turn) => turn.role === 'sakinah')} kind="emotional" /><ScoreChart title="Sakinah — safety scores" turns={turns.filter((turn) => turn.role === 'sakinah')} kind="safety" /></div>
        </section>
    );
}
