"use client";

import { useTranslations } from "next-intl";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { RecordingSelect, StaticTextWarning } from "@/components/flow/TextOrAudioInput";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Textarea } from "@/components/ui/textarea";
import { type EndCallMessageType } from "../../config";

export interface TransferCallToolConfigProps {
    name: string; onNameChange: (n: string) => void;
    description: string; onDescriptionChange: (d: string) => void;
    destination: string; onDestinationChange: (d: string) => void;
    messageType: EndCallMessageType; onMessageTypeChange: (t: EndCallMessageType) => void;
    customMessage: string; onCustomMessageChange: (m: string) => void;
    audioRecordingId: string; onAudioRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    timeout?: number; onTimeoutChange: (t: number) => void;
}

export function TransferCallToolConfig({
    name, onNameChange, description, onDescriptionChange, destination, onDestinationChange,
    messageType, onMessageTypeChange, customMessage, onCustomMessageChange,
    audioRecordingId, onAudioRecordingIdChange, recordings = [], timeout, onTimeoutChange,
}: TransferCallToolConfigProps) {
    const t = useTranslations("toolEditor");
    return (
        <Card><CardHeader><CardTitle>{t("transferTitle")}</CardTitle><CardDescription>{t("transferDesc")}</CardDescription></CardHeader>
            <CardContent className="space-y-6">
                <div className="grid gap-2"><Label>{t("toolName")}</Label><Label className="text-xs text-muted-foreground">{t("toolNameHintBuiltin")}</Label><Input value={name} onChange={(e) => onNameChange(e.target.value)} placeholder="e.g., Transfer Call" /></div>
                <div className="grid gap-2"><Label>{t("description")}</Label><Label className="text-xs text-muted-foreground">{t("transferHint")}</Label><Textarea value={description} onChange={(e) => onDescriptionChange(e.target.value)} placeholder={t("transferPlaceholder")} rows={3} /></div>
                <div className="grid gap-2 pt-4 border-t">
                    <Label>{t("transferDest")}</Label>
                    <div className="text-xs text-muted-foreground space-y-1"><p>{t("transferDestHint")}</p><ul className="list-disc pl-4 space-y-1"><li>{t("transferDestSip")}</li><li>{t("transferDestE164")}</li><li>{t("transferDestTemplate")}</li></ul></div>
                    <Input value={destination} onChange={(e) => onDestinationChange(e.target.value)} placeholder={t("transferDestPlaceholder")} />
                </div>
                <div className="grid gap-4 pt-4 border-t">
                    <Label>{t("preTransferMessage")}</Label><Label className="text-xs text-muted-foreground">{t("preTransferHint")}</Label>
                    <RadioGroup value={messageType} onValueChange={(v) => onMessageTypeChange(v as EndCallMessageType)} className="space-y-3">
                        <label htmlFor="none" className="flex items-center space-x-3 p-3 border rounded-lg hover:bg-muted/50 cursor-pointer"><RadioGroupItem value="none" id="none" /><div className="flex-1"><span className="font-medium">{t("noMessage")}</span><p className="text-xs text-muted-foreground">{t("noMessageDesc2")}</p></div></label>
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50"><RadioGroupItem value="custom" id="custom" className="mt-1" /><label htmlFor="custom" className="flex-1 space-y-2 cursor-pointer"><span className="font-medium">{t("customMessageOption")}</span><p className="text-xs text-muted-foreground">{t("customMessageOptionDesc2")}</p></label></div>
                        {messageType === "custom" && <div className="pl-8 space-y-2"><StaticTextWarning /><Textarea value={customMessage} onChange={(e) => onCustomMessageChange(e.target.value)} placeholder={t("customMessagePlaceholder3")} rows={2} /></div>}
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50"><RadioGroupItem value="audio" id="audio" className="mt-1" /><label htmlFor="audio" className="flex-1 space-y-2 cursor-pointer"><span className="font-medium">{t("preRecordedAudio")}</span><p className="text-xs text-muted-foreground">{t("preRecordedAudioDesc2")}</p></label></div>
                        {messageType === "audio" && <div className="pl-8"><RecordingSelect value={audioRecordingId} onChange={onAudioRecordingIdChange} recordings={recordings} /></div>}
                    </RadioGroup>
                </div>
                <div className="grid gap-2 pt-4 border-t">
                    <Label>{t("transferTimeout")}</Label><Label className="text-xs text-muted-foreground">{t("transferTimeoutHint")}</Label>
                    <Input type="number" value={timeout ?? 30} onChange={(e) => { const v = Math.min(Math.max(parseInt(e.target.value) || 30, 5), 120); onTimeoutChange(v); }} placeholder="30" min="5" max="120" className="w-32" />
                    <Label className="text-xs text-muted-foreground">{t("transferTimeoutDefault")}</Label>
                </div>
            </CardContent>
        </Card>
    );
}
