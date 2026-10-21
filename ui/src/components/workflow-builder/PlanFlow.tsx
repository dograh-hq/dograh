'use client';

import { ArrowRight } from 'lucide-react';
import { useId, useState } from 'react';

export function PlanFlow({ steps }: { steps: string[] }) {
    const [selected, setSelected] = useState<number | null>(null);
    const detailId = useId();

    return <section aria-label="Conversation flow" className="min-w-0 rounded-xl border bg-muted/30 p-3">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <h3 className="text-xs font-medium">Conversation flow <span className="font-normal text-muted-foreground">· {steps.length} {steps.length === 1 ? 'step' : 'steps'}</span></h3>
            <p className="text-xs text-muted-foreground">Scroll to explore · Select a step</p>
        </div>
        <ol className="flex items-center overflow-x-auto pb-2" aria-label="Conversation steps">
            {steps.map((step, index) => <li key={index} className="flex shrink-0 items-center">
                {index > 0 && <ArrowRight aria-hidden="true" className="mx-2 h-4 w-4 shrink-0 text-muted-foreground/60" />}
                <button
                    type="button"
                    aria-label={`Step ${index + 1}: ${step}`}
                    aria-expanded={selected === index}
                    aria-controls={detailId}
                    onClick={() => setSelected(selected === index ? null : index)}
                    className={`w-32 rounded-lg border px-3 py-2.5 text-left shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected === index ? 'border-primary bg-primary/5' : 'border-border bg-background hover:border-primary/50'}`}
                >
                    <span className="mb-1 block text-[10px] font-medium text-muted-foreground">{String(index + 1).padStart(2, '0')}</span>
                    <span className="line-clamp-2 min-h-8 break-words text-xs font-medium leading-4">{step}</span>
                </button>
            </li>)}
        </ol>
        <div id={detailId} hidden={selected === null} className="mt-2 border-t pt-3 text-sm leading-5">
            {selected !== null && <><span className="font-medium">Step {selected + 1}. </span><span className="whitespace-pre-wrap break-words">{steps[selected]}</span></>}
        </div>
    </section>;
}
