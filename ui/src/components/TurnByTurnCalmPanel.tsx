'use client';

import { Download } from 'lucide-react';

import { Button } from '@/components/ui/button';

type ScoreValue = number | { score?: number | null; confidence?: number | null } | null;

export type CalmTurn = {
    role?: string;
    turn_id?: number | string;
    text?: string;
    utterance_verbatim?: string;
    scores?: Record<string, ScoreValue>;
    calm_scores?: Record<string, ScoreValue>;
    emotional_scores?: Record<string, ScoreValue>;
    safety_scores?: Record<string, ScoreValue>;
    trend?: Record<string, unknown>;
    prompt_sent_to_llm?: string;
};

type Trend = { direction?: string; delta_previous?: number | null };

function valuesFor(turn: CalmTurn, kind: 'emotional' | 'safety'): Record<string, number | null> {
    const source = kind === 'safety' ? turn.safety_scores : turn.emotional_scores ?? turn.scores ?? turn.calm_scores;
    return Object.fromEntries(Object.entries(source ?? {}).map(([name, raw]) => [
        name, typeof raw === 'number' ? raw : raw?.score ?? null,
    ]));
}

function trendFor(turn: CalmTurn, name: string): Trend | undefined {
    const direct = turn.trend?.[name];
    if (direct && typeof direct === 'object') return direct as Trend;
    const parameters = turn.trend?.parameters;
    if (parameters && typeof parameters === 'object') return (parameters as Record<string, Trend>)[name];
    return undefined;
}

function trendLabel(trend?: Trend): string {
    if (!trend || trend.direction === 'insufficient_data' || trend.delta_previous == null) return '—';
    if (trend.direction === 'unchanged' || trend.delta_previous === 0) return '=';
    return `${trend.delta_previous > 0 ? '↑ +' : '↓ '}${trend.delta_previous.toFixed(1)}`;
}

function ScoreChart({ title, turns, kind }: { title: string; turns: CalmTurn[]; kind: 'emotional' | 'safety' }) {
    const names = Array.from(new Set(turns.flatMap((turn) => Object.keys(valuesFor(turn, kind)))));
    const width = 600, height = 230, left = 36, right = 12, top = 18, bottom = 32;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const x = (index: number) => left + (turns.length <= 1 ? plotWidth / 2 : index * plotWidth / (turns.length - 1));
    const y = (score: number) => top + plotHeight * (1 - Math.max(0, Math.min(10, score)) / 10);
    const colorFor = (index: number) => `hsl(${Math.round(index * 137.508) % 360} 72% 42%)`;

    return <div className="min-w-0 rounded-lg border bg-background/60 p-3">
        <p className="mb-2 text-sm font-semibold">{title}</p>
        {turns.length && names.length ? <>
            <div className="overflow-x-auto"><svg viewBox={`0 0 ${width} ${height}`} className="min-w-[540px]" role="img" aria-label={`${title} score trajectory`}>
                {[0, 2, 4, 6, 8, 10].map((tick) => <g key={tick}>
                    <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} stroke="currentColor" className="text-border" strokeDasharray="3 3" />
                    <text x={left - 7} y={y(tick) + 4} textAnchor="end" fontSize="10" fill="currentColor" className="text-muted-foreground">{tick}</text>
                </g>)}
                {turns.map((turn, index) => <text key={index} x={x(index)} y={height - 9} textAnchor="middle" fontSize="10" fill="currentColor" className="text-muted-foreground">{String(turn.turn_id ?? index + 1)}</text>)}
                {names.map((name, seriesIndex) => {
                    const points = turns.flatMap((turn, index) => {
                        const value = valuesFor(turn, kind)[name];
                        return typeof value === 'number' ? [{ index, value }] : [];
                    });
                    return <g key={name}>
                        {points.length > 1 && <polyline points={points.map(({ index, value }) => `${x(index)},${y(value)}`).join(' ')} fill="none" stroke={colorFor(seriesIndex)} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />}
                        {points.map(({ index, value }) => <circle key={index} cx={x(index)} cy={y(value)} r="3" fill={colorFor(seriesIndex)} />)}
                    </g>;
                })}
            </svg></div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">{names.map((name, index) => <span key={name} className="inline-flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: colorFor(index) }} />{name.replaceAll('_', ' ')}</span>)}</div>
            <p className="mt-1 text-[11px] text-muted-foreground">Y: score (0–10) · X: turn number</p>
        </> : <p className="text-xs text-muted-foreground">No {kind} scores for this role yet.</p>}
    </div>;
}

export function TurnByTurnCalmPanel({ turns, title = 'Turn-by-turn CALM scoring' }: { turns: CalmTurn[]; title?: string }) {
    const ordered = [...turns].sort((a, b) => Number(a.turn_id ?? 0) - Number(b.turn_id ?? 0) || (a.role === 'sakinah' ? 1 : -1));
    const callerTurns = turns.filter((turn) => turn.role !== 'sakinah');
    const sakinahTurns = turns.filter((turn) => turn.role === 'sakinah');
    const download = () => {
        const blob = new Blob([JSON.stringify(turns, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = 'calm-turn-by-turn.json';
        anchor.click();
        URL.revokeObjectURL(url);
    };

    return <section className="min-w-0 space-y-5 rounded-xl border border-border bg-card p-4" aria-label={title}>
        <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h3 className="font-semibold">{title}</h3><p className="text-xs text-muted-foreground">Scores, movement, safety, and the exact engineered prompt for each turn.</p></div>
            <Button size="sm" variant="outline" onClick={download} disabled={!turns.length}><Download className="mr-2 h-4 w-4" />Download scoring</Button>
        </div>
        {ordered.length ? <div className="overflow-hidden rounded-lg border">{ordered.map((turn, index) => <article key={`${turn.role ?? 'caller'}-${String(turn.turn_id ?? index)}-${index}`} className={`space-y-3 p-4 ${index % 2 ? 'bg-muted/35' : 'bg-background'}`}>
            <div className="flex flex-wrap items-baseline gap-2"><span className="font-semibold capitalize">{turn.role === 'sakinah' ? 'Sakinah' : 'Caller / service user'}</span><span className="text-xs text-muted-foreground">Turn {turn.turn_id ?? index + 1}</span></div>
            <p className="whitespace-pre-wrap text-sm">{turn.text ?? turn.utterance_verbatim ?? 'No transcript text'}</p>
            {(['emotional', 'safety'] as const).map((kind) => {
                const scores = valuesFor(turn, kind);
                return Object.keys(scores).length ? <div key={kind} className="space-y-1">
                    <p className="text-xs font-medium capitalize text-muted-foreground">{kind} scores</p>
                    <div className="flex flex-wrap gap-2">{Object.entries(scores).map(([name, score]) => <span key={name} className="rounded-md border bg-card px-2 py-1 text-xs tabular-nums"><span className="font-medium">{name.replaceAll('_', ' ')}</span> {score ?? '—'} <span className="font-semibold" aria-label={`${name} trend ${trendLabel(trendFor(turn, name))}`}>{trendLabel(trendFor(turn, name))}</span></span>)}</div>
                </div> : null;
            })}
            {turn.prompt_sent_to_llm && <details className="rounded-md border bg-card p-3"><summary className="cursor-pointer text-sm font-medium text-primary">Engineered prompt for turn {turn.turn_id ?? index + 1}</summary><pre className="mt-3 max-h-[32rem] w-full overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-3 text-xs leading-relaxed">{turn.prompt_sent_to_llm}</pre></details>}
        </article>)}</div> : <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">No CALM turns recorded yet. Scores appear here as the call progresses when CALM scoring is enabled for the agent.</p>}
        {ordered.length > 0 && <div className="grid min-w-0 gap-4 xl:grid-cols-2">
            <ScoreChart title="Caller / service user — emotional scores" turns={callerTurns} kind="emotional" />
            <ScoreChart title="Caller / service user — safety scores" turns={callerTurns} kind="safety" />
            <ScoreChart title="Sakinah — emotional scores" turns={sakinahTurns} kind="emotional" />
            <ScoreChart title="Sakinah — safety scores" turns={sakinahTurns} kind="safety" />
        </div>}
    </section>;
}
