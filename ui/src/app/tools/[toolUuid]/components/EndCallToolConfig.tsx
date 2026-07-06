"use client";

import { useTranslations } from "next-intl";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { RecordingSelect, StaticTextWarning } from "@/components/flow/TextOrAudioInput";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { type EndCallMessageType } from "../../config";

export interface EndCallToolConfigProps {
    name: string; onNameChange: (n: string) => void;
    description: string; onDescriptionChange: (d: string) => void;
    messageType: EndCallMessageType; onMessageTypeChange: (t: EndCallMessageType) => void;
    customMessage: string; onCustomMessageChange: (m: string) => void;
    audioRecordingId: string; onAudioRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
    endCallReason: boolean; onEndCallReasonChange: (e: boolean) => void;
    endCallReasonDescription: string; onEndCallReasonDescriptionChange: (d: string) => void;
}

export function EndCallToolConfig({
    name, onNameChange, description, onDescriptionChange, messageType, onMessageTypeChange,
    customMessage, onCustomMessageChange, audioRecordingId, onAudioRecordingIdChange,
    recordings = [], endCallReason, onEndCallReasonChange, endCallReasonDescription, onEndCallReasonDescriptionChange,
}: EndCallToolConfigProps) {
    const t = useTranslations("toolEditor");
    return (
        <Card><CardHeader><CardTitle>{t("endCallTitle")}</CardTitle><CardDescription>{t("endCallDesc")}</CardDescription></CardHeader>
            <CardContent className="space-y-6">
                <div className="grid gap-2"><Label>{t("toolName")}</Label><Label className="text-xs text-muted-foreground">{t("toolNameHintBuiltin")}</Label><Input value={name} onChange={(e) => onNameChange(e.target.value)} placeholder="e.g., End Call" /></div>
                <div className="grid gap-2"><Label>{t("description")}</Label><Label className="text-xs text-muted-foreground">{t("endCallHint")}</Label><Textarea value={description} onChange={(e) => onDescriptionChange(e.target.value)} placeholder={t("endCallPlaceholder")} rows={3} /></div>
                <div className="grid gap-2 pt-4 border-t">
                    <div className="flex items-center space-x-2"><Switch id="end-call-reason" checked={endCallReason} onCheckedChange={onEndCallReasonChange} /><Label htmlFor="end-call-reason">{t("captureReason")}</Label></div>
                    <Label className="text-xs text-muted-foreground">{t("captureReasonHint")}</Label>
                    {endCallReason && <div className="grid gap-2 pt-2"><Label>{t("reasonDescription")}</Label><Label className="text-xs text-muted-foreground">{t("reasonDescriptionHint")}</Label><Textarea value={endCallReasonDescription} onChange={(e) => onEndCallReasonDescriptionChange(e.target.value)} placeholder={t("reasonPlaceholder")} rows={2} /></div>}
                </div>
                <div className="grid gap-4 pt-4 border-t">
                    <Label>{t("goodbyeMessage")}</Label><Label className="text-xs text-muted-foreground">{t("goodbyeMessageHint")}</Label>
                    <RadioGroup value={messageType} onValueChange={(v) => onMessageTypeChange(v as EndCallMessageType)} className="space-y-3">
                        <label htmlFor="none" className="flex items-center space-x-3 p-3 border rounded-lg hover:bg-muted/50 cursor-pointer"><RadioGroupItem value="none" id="none" /><div className="flex-1"><span className="font-medium">{t("noMessage")}</span><p className="text-xs text-muted-foreground">{t("noMessageDesc")}</p></div></label>
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50"><RadioGroupItem value="custom" id="custom" className="mt-1" /><label htmlFor="custom" className="flex-1 space-y-2 cursor-pointer"><span className="font-medium">{t("customMessageOption")}</span><p className="text-xs text-muted-foreground">{t("customMessageOptionDesc")}</p></label></div>
                        {messageType === "custom" && <div className="pl-8 space-y-2"><StaticTextWarning /><Textarea value={customMessage} onChange={(e) => onCustomMessageChange(e.target.value)} placeholder={t("customMessagePlaceholder2")} rows={2} /></div>}
                        <div className="flex items-start space-x-3 p-3 border rounded-lg hover:bg-muted/50"><RadioGroupItem value="audio" id="audio" className="mt-1" /><label htmlFor="audio" className="flex-1 space-y-2 cursor-pointer"><span className="font-medium">{t("preRecordedAudio")}</span><p className="text-xs text-muted-foreground">{t("preRecordedAudioDesc")}</p></label></div>
                        {messageType === "audio" && <div className="pl-8"><RecordingSelect value={audioRecordingId} onChange={onAudioRecordingIdChange} recordings={recordings} /></div>}
                    </RadioGroup>
                </div>
            </CardContent>
        </Card>
    );
}
