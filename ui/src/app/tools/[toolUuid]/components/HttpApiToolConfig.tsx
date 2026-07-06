"use client";

import { useTranslations } from "next-intl";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { StaticTextWarning, TextOrAudioInput } from "@/components/flow/TextOrAudioInput";
import {
    CredentialSelector, type HttpMethod, HttpMethodSelector, KeyValueEditor,
    type KeyValueItem, ParameterEditor, PresetParameterEditor,
    type PresetToolParameter, type ToolParameter, UrlInput,
} from "@/components/http";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

export interface HttpApiToolConfigProps {
    name: string; onNameChange: (n: string) => void;
    description: string; onDescriptionChange: (d: string) => void;
    httpMethod: HttpMethod; onHttpMethodChange: (m: HttpMethod) => void;
    url: string; onUrlChange: (u: string) => void;
    credentialUuid: string; onCredentialUuidChange: (u: string) => void;
    headers: KeyValueItem[]; onHeadersChange: (h: KeyValueItem[]) => void;
    parameters: ToolParameter[]; onParametersChange: (p: ToolParameter[]) => void;
    presetParameters: PresetToolParameter[]; onPresetParametersChange: (p: PresetToolParameter[]) => void;
    timeoutMs: number; onTimeoutMsChange: (t: number) => void;
    customMessage: string; onCustomMessageChange: (m: string) => void;
    customMessageType: 'text' | 'audio'; onCustomMessageTypeChange: (t: 'text' | 'audio') => void;
    customMessageRecordingId: string; onCustomMessageRecordingIdChange: (id: string) => void;
    recordings?: RecordingResponseSchema[];
}

export function HttpApiToolConfig({
    name, onNameChange, description, onDescriptionChange, httpMethod, onHttpMethodChange,
    url, onUrlChange, credentialUuid, onCredentialUuidChange, headers, onHeadersChange,
    parameters, onParametersChange, presetParameters, onPresetParametersChange,
    timeoutMs, onTimeoutMsChange, customMessage, onCustomMessageChange,
    customMessageType, onCustomMessageTypeChange, customMessageRecordingId, onCustomMessageRecordingIdChange,
    recordings = [],
}: HttpApiToolConfigProps) {
    const t = useTranslations("toolEditor");
    return (
        <Card><CardHeader><CardTitle>{t("httpTitle")}</CardTitle><CardDescription>{t("httpDesc")}</CardDescription></CardHeader>
            <CardContent>
                <Tabs defaultValue="settings" className="w-full">
                    <TabsList className="grid w-full grid-cols-3">
                        <TabsTrigger value="settings">{t("tabSettings")}</TabsTrigger>
                        <TabsTrigger value="auth">{t("tabAuth")}</TabsTrigger>
                        <TabsTrigger value="parameters">{t("tabParams")}</TabsTrigger>
                    </TabsList>
                    <TabsContent value="settings" className="space-y-4 mt-4">
                        <div className="grid gap-2"><Label>{t("toolName")}</Label><Label className="text-xs text-muted-foreground">{t("toolNameHint")}</Label><Input value={name} onChange={(e) => onNameChange(e.target.value)} placeholder="e.g., Book Appointment" /></div>
                        <div className="grid gap-2"><Label>{t("description")}</Label><Label className="text-xs text-muted-foreground">{t("descriptionHint")}</Label><Textarea value={description} onChange={(e) => onDescriptionChange(e.target.value)} placeholder={t("descriptionPlaceholder")} rows={3} /></div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2"><Label>{t("httpMethod")}</Label><HttpMethodSelector value={httpMethod} onChange={onHttpMethodChange} /></div>
                            <div className="grid gap-2"><Label>{t("timeout")}</Label><Input type="number" value={timeoutMs} onChange={(e) => onTimeoutMsChange(parseInt(e.target.value) || 5000)} min={1000} max={30000} /></div>
                        </div>
                        <div className="grid gap-2"><Label>{t("endpointUrl")}</Label><UrlInput value={url} onChange={onUrlChange} placeholder="https://api.example.com/appointments" showValidation /></div>
                        <div className="grid gap-2 pt-4 border-t"><Label>{t("customMessage")}</Label><Label className="text-xs text-muted-foreground">{t("customMessageHint")}</Label>
                            <TextOrAudioInput type={customMessageType} onTypeChange={onCustomMessageTypeChange} recordingId={customMessageRecordingId} onRecordingIdChange={onCustomMessageRecordingIdChange} recordings={recordings}>
                                <><StaticTextWarning /><Textarea value={customMessage} onChange={(e) => onCustomMessageChange(e.target.value)} placeholder={t("customMessagePlaceholder")} rows={2} /></>
                            </TextOrAudioInput>
                        </div>
                    </TabsContent>
                    <TabsContent value="auth" className="space-y-4 mt-4"><CredentialSelector value={credentialUuid} onChange={onCredentialUuidChange} /></TabsContent>
                    <TabsContent value="parameters" className="space-y-4 mt-4">
                        <div className="grid gap-2"><Label>{t("llmParams")}</Label><Label className="text-xs text-muted-foreground">{t("llmParamsHint")}</Label><ParameterEditor parameters={parameters} onChange={onParametersChange} /></div>
                        <div className="grid gap-2 pt-4 border-t"><Label>{t("presetParams")}</Label><Label className="text-xs text-muted-foreground">{t("presetParamsHint")}</Label><PresetParameterEditor parameters={presetParameters} onChange={onPresetParametersChange} /></div>
                        <div className="grid gap-2 pt-4 border-t"><Label>{t("customHeaders")}</Label><Label className="text-xs text-muted-foreground">{t("customHeadersHint")}</Label>
                            <KeyValueEditor items={headers} onChange={onHeadersChange} keyPlaceholder="Header name" valuePlaceholder="Header value" addButtonText={t("addHeader")} />
                        </div>
                    </TabsContent>
                </Tabs>
            </CardContent>
        </Card>
    );
}
