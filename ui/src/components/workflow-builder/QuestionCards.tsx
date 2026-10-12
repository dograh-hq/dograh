'use client';

import { useState } from 'react';

import type { BuilderInterrupt } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';

type Answers = Record<string, Record<string, string[]>>;

export function QuestionCards({ pending, disabled, onSubmit }: {
    pending: BuilderInterrupt[]; disabled: boolean; onSubmit: (answers: Answers) => void;
}) {
    const [selected, setSelected] = useState<Record<string, string[]>>({});
    const [custom, setCustom] = useState<Record<string, string>>({});
    const questions = pending.flatMap(item => (item.questions ?? []).map(question => ({ interrupt: item.id, question, key: `${item.id}:${question.id}` })));
    const valuesFor = (key: string, multiple: boolean) => Array.from(new Set([
        ...(selected[key] ?? []),
        ...(multiple ? (custom[key] ?? '').split('\n').map(value => value.trim()).filter(Boolean) : custom[key]?.trim() ? [custom[key].trim()] : []),
    ]));
    const complete = questions.every(({ key, question }) => valuesFor(key, question.kind === 'multiple').length > 0);
    return <form className="space-y-6 rounded-2xl border bg-background p-5" onSubmit={event => {
        event.preventDefault();
        if (!complete || disabled) return;
        const answers: Answers = {};
        for (const { interrupt, question, key } of questions) {
            answers[interrupt] ??= {};
            answers[interrupt][question.id] = valuesFor(key, question.kind === 'multiple');
        }
        onSubmit(answers);
    }}>
        {questions.map(({ question, key }) => <fieldset key={key} disabled={disabled} className="space-y-3">
            <legend className="mb-2 text-sm font-medium">{question.title}</legend>
            {question.kind === 'multiple' && <p className="text-xs text-muted-foreground">Select all that apply.</p>}
            {question.kind !== 'text' && <div className="flex flex-wrap gap-2">
                {(question.options ?? []).map(option => <button key={option} type="button" aria-pressed={(selected[key] ?? []).includes(option)}
                    className={`rounded-xl border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 ${(selected[key] ?? []).includes(option) ? 'border-primary bg-primary/10' : 'hover:bg-muted'}`}
                    onClick={() => {
                        setSelected(current => ({ ...current, [key]: question.kind === 'multiple'
                            ? (current[key] ?? []).includes(option) ? (current[key] ?? []).filter(value => value !== option) : [...(current[key] ?? []), option]
                            : [option] }));
                        if (question.kind === 'single') setCustom(current => ({ ...current, [key]: '' }));
                    }}>{option}</button>)}
            </div>}
            {(question.allow_custom || question.kind === 'text') && (question.kind === 'text' || question.kind === 'multiple'
                ? <Textarea aria-label={question.kind === 'text' ? question.title : `Custom answer: ${question.title}`} placeholder={question.kind === 'multiple' ? 'Add your own answers, one per line…' : 'Your answer…'} maxLength={2000} value={custom[key] ?? ''} onChange={event => setCustom(current => ({ ...current, [key]: event.target.value }))} />
                : <Input aria-label={`Custom answer: ${question.title}`} placeholder="Or type your own answer…" maxLength={2000} value={custom[key] ?? ''}
                    onChange={event => {
                        setCustom(current => ({ ...current, [key]: event.target.value }));
                        if (question.kind === 'single') setSelected(current => ({ ...current, [key]: [] }));
                    }} />)}
        </fieldset>)}
        <Button type="submit" disabled={!complete || disabled}>Continue</Button>
    </form>;
}
