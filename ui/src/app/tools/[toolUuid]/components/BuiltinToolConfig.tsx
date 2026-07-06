"use client";

import { useTranslations } from "next-intl";
import type { RecordingResponseSchema } from "@/client/types.gen";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export interface BuiltinToolConfigProps {
    name: string;
    onNameChange: (name: string) => void;
    description: string;
    onDescriptionChange: (description: string) => void;
    title: string;
    subtitle: string;
}

export function BuiltinToolConfig({
    name, onNameChange, description, onDescriptionChange, title, subtitle,
}: BuiltinToolConfigProps) {
    const t = useTranslations("toolEditor");
    return (
        <Card>
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                <CardDescription>{subtitle}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                <div className="space-y-2">
                    <Label htmlFor="tool-name">{t("toolName")}</Label>
                    <Input id="tool-name" value={name} onChange={(e) => onNameChange(e.target.value)} placeholder={t("toolNamePlaceholder")} />
                </div>
                <div className="space-y-2">
                    <Label htmlFor="tool-description">{t("description")}</Label>
                    <p className="text-xs text-muted-foreground">{t("descriptionHint")}</p>
                    <Textarea id="tool-description" value={description} onChange={(e) => onDescriptionChange(e.target.value)} placeholder={t("descriptionPlaceholderBuiltin")} rows={3} />
                </div>
            </CardContent>
        </Card>
    );
}
