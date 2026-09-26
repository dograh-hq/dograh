"use client";

import { AlertTriangle, ChevronDown, Loader2 } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import type {
    InferredParameter,
    ParameterGroup,
    SakinahEvaluationResult,
    ServiceUserEvaluationResult,
    Trend,
    TurnEvaluation,
} from "./calmTypes";

const SERVICE_USER_CALM_PRIORITY = [
    "emotional_intensity", "hopelessness", "engagement", "cognitive_overwhelm",
];
const SERVICE_USER_SAFETY_PRIORITY = [
    "suicidal_ideation", "suicidal_intent", "self_harm",
];
const SAKINAH_QUALITY_PRIORITY = ["empathy", "emotional_attunement", "validation"];
const SAKINAH_SAFETY_PRIORITY = ["risk_recognition", "clarification_quality"];

function labelFor(name: string): string {
    return name.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}
function scoreClass(score: number, positive: boolean): string {
    const severity = positive ? 10 - score : score;
    if (severity >= 7) return "border-destructive/50 bg-destructive/10 text-destructive";
    if (severity >= 4) return "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400";
    return "border-border bg-muted/60 text-foreground";
}

function Score({ name, value, positive = false, trend }: { name: string; value: InferredParameter; positive?: boolean; trend?: Trend }) {
    return <div className="rounded-md border bg-background/50 p-2.5">
        <div className="flex items-start justify-between gap-2">
            <span className="text-xs font-medium">{labelFor(name)}</span>
            <Badge variant="outline" className={cn("shrink-0", scoreClass(value.score, positive))}>{value.score} / 10</Badge>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground">confidence {value.confidence}</p>
        {trend ? <p className="text-[11px] text-muted-foreground">trend {labelFor(trend).toLowerCase()}</p> : null}
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{value.evidence}</p>
    </div>;
}

function ScoreGroup({ values, priority, positive = false, trends }: { values: ParameterGroup; priority: string[]; positive?: boolean; trends?: Record<string, Trend> }) {
    const [expanded, setExpanded] = useState(false);
    const priorityEntries = priority.flatMap((name) => values[name] ? [[name, values[name]] as const] : []);
    const otherEntries = Object.entries(values).filter(([name]) => !priority.includes(name));
    return <Collapsible open={expanded} onOpenChange={setExpanded}>
        <div className="grid gap-2 sm:grid-cols-2">{priorityEntries.map(([name, value]) => <Score key={name} name={name} value={value} positive={positive} trend={trends?.[name]} />)}</div>
        {otherEntries.length ? <>
            <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="sm" className="mt-2"><ChevronDown className={cn("transition-transform", expanded && "rotate-180")} />{expanded ? "Hide full score set" : "Show full score set"}</Button></CollapsibleTrigger>
            <CollapsibleContent className="grid gap-2 pt-2 sm:grid-cols-2">{otherEntries.map(([name, value]) => <Score key={name} name={name} value={value} positive={positive} trend={trends?.[name]} />)}</CollapsibleContent>
        </> : null}
    </Collapsible>;
}

function ProseList({ title, items }: { title: string; items: string[] }) {
    if (!items.length) return null;
    return <div><h5 className="text-xs font-semibold">{title}</h5><ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-muted-foreground">{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul></div>;
}

function ServiceUserPanel({ result }: { result: ServiceUserEvaluationResult }) {
    const calm = {
        ...result.state.emotion,
        ...result.state.mental_state,
        ...result.state.interaction,
        ...result.state.capacity,
    };
    return <Tabs defaultValue="calm" className="mt-3">
        <TabsList className="h-8"><TabsTrigger value="calm">Live CALM</TabsTrigger><TabsTrigger value="safety">Safety</TabsTrigger><TabsTrigger value="clinical">Clinical evaluation</TabsTrigger></TabsList>
        <TabsContent value="calm" className="pt-2"><ScoreGroup values={calm} priority={SERVICE_USER_CALM_PRIORITY} trends={result.trend} /></TabsContent>
        <TabsContent value="safety" className="pt-2"><ScoreGroup values={result.safety} priority={SERVICE_USER_SAFETY_PRIORITY} trends={result.trend} /></TabsContent>
        <TabsContent value="clinical" className="space-y-3 rounded-md border bg-background/50 p-3 text-sm"><p>{result.clinical_evaluation.summary}</p><ProseList title="Salient changes" items={result.clinical_evaluation.salient_changes} /><ProseList title="Uncertainties" items={result.clinical_evaluation.uncertainties} /><ProseList title="Recommended clarification" items={result.clinical_evaluation.recommended_clarification} /></TabsContent>
    </Tabs>;
}

function SakinahPanel({ result }: { result: SakinahEvaluationResult }) {
    const { critical_flags: criticalFlags, ...safetyScores } = result.safety_evaluation;
    return <div className="mt-3 space-y-2">
        {criticalFlags.length ? <div role="alert" className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-destructive"><div className="flex items-center gap-2 text-xs font-semibold"><AlertTriangle className="size-4" />Critical safety flags</div><ul className="mt-2 list-disc space-y-1 pl-4 text-xs">{criticalFlags.map((flag, index) => <li key={`flag-${index}`}>{flag}</li>)}</ul></div> : null}
        <Tabs defaultValue="calm"><TabsList className="h-8"><TabsTrigger value="calm">Live CALM</TabsTrigger><TabsTrigger value="safety">Safety</TabsTrigger><TabsTrigger value="clinical">Clinical evaluation</TabsTrigger></TabsList>
            <TabsContent value="calm" className="pt-2"><ScoreGroup values={result.response_quality} priority={SAKINAH_QUALITY_PRIORITY} positive /></TabsContent>
            <TabsContent value="safety" className="pt-2"><ScoreGroup values={safetyScores} priority={SAKINAH_SAFETY_PRIORITY} positive /></TabsContent>
            <TabsContent value="clinical" className="space-y-3 rounded-md border bg-background/50 p-3 text-sm"><p>{result.clinical_evaluation.summary}</p><ProseList title="Strengths" items={result.clinical_evaluation.strengths} /><ProseList title="Missed opportunities" items={result.clinical_evaluation.missed_opportunities} /><ProseList title="Recommended next approach" items={result.clinical_evaluation.recommended_next_approach} /></TabsContent>
        </Tabs>
    </div>;
}

export function CalmEvaluationPanel({ evaluation }: { evaluation?: TurnEvaluation }) {
    if (!evaluation || evaluation.status === "pending") return <div className="mt-3 flex items-center gap-2 border-t pt-3 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Scoring...</div>;
    if (evaluation.status === "failed" || !evaluation.result) return <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">Evaluation unavailable</p>;
    return evaluation.result.role === "service_user" ? <ServiceUserPanel result={evaluation.result} /> : <SakinahPanel result={evaluation.result} />;
}
