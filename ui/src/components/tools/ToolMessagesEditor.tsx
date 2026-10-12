"use client";

import { Plus, Trash2 } from "lucide-react";
import { useId } from "react";

import type { RecordingResponseSchema } from "@/client/types.gen";
import { StaticTextWarning, TextOrAudioInput } from "@/components/flow/TextOrAudioInput";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { type MessageVariant, type ToolMessagesForm, validateToolMessages } from "./toolMessages";

type Props = {
    value: ToolMessagesForm;
    onChange: (value: ToolMessagesForm) => void;
    recordings: RecordingResponseSchema[];
    timeoutMs?: number;
};

function Variants({ value, onChange, recordings, label }: {
    value: MessageVariant[];
    onChange: (value: MessageVariant[]) => void;
    recordings: RecordingResponseSchema[];
    label: string;
}) {
    const id = useId();
    const update = (index: number, next: MessageVariant) => onChange(value.map((item, i) => i === index ? next : item));
    return (
        <div className="space-y-3">
            {value.map((variant, index) => (
                <div key={index} className="rounded-md border p-3 space-y-2">
                    <div className="flex items-center justify-between">
                        <Label htmlFor={`${id}-${index}`}>Variant {index + 1}</Label>
                        <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${label} variant ${index + 1}`}
                            onClick={() => onChange(value.filter((_, i) => i !== index))}>
                            <Trash2 className="size-4" />
                        </Button>
                    </div>
                    <TextOrAudioInput
                        type={variant.type}
                        onTypeChange={(type) => update(index, type === "text" ? { type, text: "" } : { type, recording_id: 0 })}
                        recordingId={variant.type === "audio" && variant.recording_id ? String(variant.recording_id) : ""}
                        onRecordingIdChange={(recordingId) => update(index, { type: "audio", recording_id: Number(recordingId) })}
                        recordings={recordings}
                    >
                        <Textarea id={`${id}-${index}`} aria-label={`${label} variant ${index + 1}`}
                            value={variant.type === "text" ? variant.text : ""} rows={2}
                            placeholder="e.g., Let me check that for you."
                            onChange={(event) => update(index, { type: "text", text: event.target.value })} />
                    </TextOrAudioInput>
                </div>
            ))}
            <Button type="button" variant="outline" size="sm" disabled={value.length >= 20}
                onClick={() => onChange([...value, { type: "text", text: "" }])}>
                <Plus className="mr-2 size-4" />Add {label} variant
            </Button>
        </div>
    );
}

export function ToolMessagesEditor({ value, onChange, recordings, timeoutMs }: Props) {
    const id = useId();
    const delayed = value.delayed ?? [];
    const error = validateToolMessages(value);
    return (
        <Card className="mt-6">
            <CardHeader>
                <CardTitle>Messages</CardTitle>
                <CardDescription>
                    One variant is chosen randomly for each update. The tool runs while the message plays.
                    When tools run together, only the first called tool with messages supplies announcements.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <StaticTextWarning />
                <section className="space-y-3" aria-labelledby={`${id}-start`}>
                    <h3 id={`${id}-start`} className="font-medium">When the tool starts</h3>
                    {!value.start && <p className="text-sm text-muted-foreground">No start message configured.</p>}
                    <Variants value={value.start?.variants ?? []} recordings={recordings} label="start"
                        onChange={(variants) => onChange({ ...value, start: variants.length ? { variants } : null })} />
                </section>
                <section className="space-y-3 border-t pt-5" aria-labelledby={`${id}-delayed`}>
                    <h3 id={`${id}-delayed`} className="font-medium">While the tool is running</h3>
                    <p className="text-sm text-muted-foreground">
                        Each update plays at most once, after the configured time from execution start.
                        Pending updates stop when this tool finishes.
                    </p>
                    {delayed.map((message, index) => (
                        <div key={index} className="rounded-lg border p-4 space-y-3">
                            <div className="flex items-center justify-between gap-3">
                                <div className="flex items-center gap-2">
                                    <Label htmlFor={`${id}-delay-${index}`}>After</Label>
                                    <Input id={`${id}-delay-${index}`} aria-label={`Delay ${index + 1} in milliseconds`}
                                        type="number" min={1} max={120000} step={1} className="w-28" value={message.after_ms || ""}
                                        onChange={(event) => onChange({ ...value, delayed: delayed.map((item, i) => i === index ? { ...item, after_ms: Number(event.target.value) } : item) })} />
                                    <span className="text-sm text-muted-foreground">ms</span>
                                </div>
                                <Button type="button" variant="ghost" size="sm" aria-label={`Remove delayed update ${index + 1}`}
                                    onClick={() => onChange({ ...value, delayed: delayed.filter((_, i) => i !== index) })}>
                                    <Trash2 className="size-4" />
                                </Button>
                            </div>
                            {timeoutMs != null && message.after_ms >= timeoutMs && (
                                <p className="text-sm text-amber-700">This update is at or beyond the tool timeout and will not play.</p>
                            )}
                            <Variants value={message.variants} recordings={recordings} label={`delayed ${index + 1}`}
                                onChange={(variants) => onChange({ ...value, delayed: delayed.map((item, i) => i === index ? { ...item, variants } : item) })} />
                        </div>
                    ))}
                    <Button type="button" variant="outline" size="sm" disabled={delayed.length >= 10}
                        onClick={() => {
                            const timings = new Set(delayed.map((item) => item.after_ms));
                            let after_ms = Math.min(120000, Math.max(0, ...timings) + 3000);
                            if (timings.has(after_ms)) {
                                after_ms = 3000;
                                while (timings.has(after_ms)) after_ms += 3000;
                            }
                            onChange({ ...value, delayed: [...delayed, { after_ms, variants: [{ type: "text", text: "" }] }] });
                        }}>
                        <Plus className="mr-2 size-4" />Add delayed update
                    </Button>
                </section>
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <p className="text-sm text-muted-foreground">After the tool finishes, the agent responds using the tool result.</p>
            </CardContent>
        </Card>
    );
}
