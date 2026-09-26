import { cn } from "@/lib/utils";

import type { TranscriptTurn } from "./types";

interface TranscriptPanelProps {
    turns: TranscriptTurn[];
}
export function TranscriptPanel({ turns }: TranscriptPanelProps) {
    return (
        <section className="flex min-h-[28rem] flex-col rounded-xl border bg-card shadow-sm">
            <div className="border-b px-5 py-4">
                <h2 className="text-lg font-semibold">Live transcript</h2>
                <p className="text-sm text-muted-foreground">
                    Interim speech appears dimmed until recognition is final.
                </p>
            </div>
            <div aria-live="polite" className="flex-1 space-y-4 overflow-y-auto p-5">
                {turns.length === 0 ? (
                    <p className="pt-16 text-center text-sm text-muted-foreground">
                        The conversation will appear here.
                    </p>
                ) : (
                    turns.map((turn, index) => (
                        <article
                            key={`${turn.timestamp}-${index}`}
                            className={cn(
                                "rounded-lg border p-3",
                                turn.role === "user" ? "mr-8 bg-muted/40" : "ml-8 bg-primary/5",
                                !turn.final && "border-dashed opacity-60",
                            )}
                        >
                            <div className="mb-1 flex items-center justify-between gap-3">
                                <span className="text-xs font-semibold tracking-wide">
                                    {turn.role === "user" ? "USER" : "SAKINAH"}
                                </span>
                                {!turn.final ? (
                                    <span className="text-xs text-muted-foreground">pending</span>
                                ) : null}
                            </div>
                            <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.text}</p>
                        </article>
                    ))
                )}
            </div>
        </section>
    );
}
