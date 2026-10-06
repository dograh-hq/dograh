import { useEffect, useId, useState } from "react";

import { listRecordingsApiV1WorkflowRecordingsGet } from "@/client/sdk.gen";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { RecordingSelect } from "@/components/flow/TextOrAudioInput";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import type { AnswerMessage, AnswerSupervisorSettings } from "@/types/workflow-configurations";

const selectClass = "w-full rounded-md border bg-background p-2 text-sm";

export function readAnswerSupervisorSettings(config: AnswerSupervisorSettings): AnswerSupervisorSettings {
    const { listening_window_ms, human_utterance_max_ms,
        machine_utterance_cap_ms, classify_budget_ms, screening_wait_ms,
        max_screening_rearms, voicemail_action = "hangup", voicemail_message, screening_message } = config;
    return { listening_window_ms, human_utterance_max_ms,
        machine_utterance_cap_ms, classify_budget_ms, screening_wait_ms,
        max_screening_rearms, voicemail_action, voicemail_message, screening_message };
}

export function isVoicemailMessageMissing(config: AnswerSupervisorSettings): boolean {
    const message = config.voicemail_message;
    return config.voicemail_action === "leave_message" &&
        !Boolean(message?.text?.trim() || message?.recording_id || message?.recording_pk);
}

function MessageField({ label, value = {}, onChange, recordings }: {
    label: string;
    value?: AnswerMessage;
    onChange: (value: AnswerMessage) => void;
    recordings: RecordingResponseSchema[];
}) {
    const id = useId();
    const [audio, setAudio] = useState(Boolean(value.recording_id || value.recording_pk));
    const selected = value.recording_pk ?? recordings.find(r => r.recording_id === value.recording_id)?.id;
    return <fieldset className="space-y-2 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">{label}</legend>
        <Label htmlFor={`${id}-format`}>Message format</Label>
        <select id={`${id}-format`} className={selectClass} value={audio ? "audio" : "text"}
            onChange={e => {
                setAudio(e.target.value === "audio");
                if (e.target.value === "text") onChange({ text: value.text || "" });
                else onChange({ text: value.text, recording_id: value.recording_id, recording_pk: value.recording_pk });
            }}>
            <option value="text">Text</option>
            <option value="audio">Recording</option>
        </select>
        {audio ? <RecordingSelect value={selected ? String(selected) : ""} recordings={recordings}
            onChange={pk => onChange({ recording_pk: pk ? Number(pk) : undefined })} />
            : <>
                <Label htmlFor={id} className="sr-only">{label}</Label>
                <Textarea id={id} maxLength={5000} rows={3} value={value.text || ""}
                    placeholder={label === "Voicemail message" ? "Hi, this is Alex from Acme. Please call us back at..." : "Alex from Acme, calling about your appointment."}
                    onChange={e => onChange({ text: e.target.value })} />
            </>}
    </fieldset>;
}

export function AnswerSupervisorFields({ value, onChange }: {
    value: AnswerSupervisorSettings;
    onChange: (value: AnswerSupervisorSettings) => void;
}) {
    const id = useId();
    const { user, loading: authLoading } = useAuth();
    const [recordings, setRecordings] = useState<RecordingResponseSchema[]>([]);
    const [error, setError] = useState("");

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        setError("");
        listRecordingsApiV1WorkflowRecordingsGet({ query: {} }).then(response => {
            if (cancelled) return;
            if (response.error || !response.data) {
                setError(detailFromError(response.error, "Could not load recordings."));
                return;
            }
            setRecordings(response.data.recordings);
        }).catch(() => {
            if (!cancelled) setError("Could not load recordings.");
        });
        return () => { cancelled = true; };
    }, [authLoading, user]);

    return <div className="space-y-4">
        <div className="space-y-3">
            <Label id={`${id}-voicemail-action`}>When voicemail is detected</Label>
            <RadioGroup aria-labelledby={`${id}-voicemail-action`} value={value.voicemail_action ?? "hangup"}
                onValueChange={action => {
                    if (action === "hangup" || action === "leave_message") {
                        onChange({ ...value, voicemail_action: action });
                    }
                }}>
                <div className="flex items-center gap-2">
                    <RadioGroupItem id={`${id}-hangup`} value="hangup" />
                    <Label htmlFor={`${id}-hangup`}>Disconnect the call</Label>
                </div>
                <div className="flex items-center gap-2">
                    <RadioGroupItem id={`${id}-leave-message`} value="leave_message" />
                    <Label htmlFor={`${id}-leave-message`}>Leave a message</Label>
                </div>
            </RadioGroup>
        </div>
        {value.voicemail_action === "leave_message" && <>
            <MessageField label="Voicemail message" value={value.voicemail_message} recordings={recordings}
                onChange={message => onChange({ ...value, voicemail_message: message })} />
            <p className="text-xs text-muted-foreground">The agent plays this message, then disconnects the call.</p>
            {isVoicemailMessageMissing(value) && <p role="alert" className="text-sm text-destructive">Enter a voicemail message or select a recording.</p>}
        </>}
        <MessageField label="Screening message" value={value.screening_message} recordings={recordings}
            onChange={message => onChange({ ...value, screening_message: message })} />
        <p className="text-xs text-muted-foreground">State your name and reason for calling. After this message, the agent waits silently for the person to answer. Leave blank to end screened calls.</p>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">Timing</summary>
            <div className="mt-3 space-y-2">
                <p className="text-xs text-muted-foreground">Use the Start node’s Delayed Start setting to configure how long the agent listens before greeting a silent answer. The default is 1.2 seconds. A brief human greeting can end the wait sooner.</p>
                <Label htmlFor={`${id}-screening`}>Screening wait (seconds)</Label>
                <Input id={`${id}-screening`} type="number" min="1" max="60" step="1"
                    value={(value.screening_wait_ms ?? 30000) / 1000}
                    onChange={e => onChange({ ...value, screening_wait_ms: Math.round(Math.min(60, Math.max(1, Number(e.target.value) || 30)) * 1000) })} />
                <p className="text-xs text-muted-foreground">How long to wait for the person after the screening message finishes.</p>
            </div>
        </details>
    </div>;
}
