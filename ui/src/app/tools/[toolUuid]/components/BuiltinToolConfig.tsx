"use client";

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
    name,
    onNameChange,
    description,
    onDescriptionChange,
    title,
    subtitle,
}: BuiltinToolConfigProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>{title}</CardTitle>
                <CardDescription>{subtitle}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Tool Name */}
                <div className="space-y-2">
                    <Label htmlFor="tool-name">Tool Name</Label>
                    <Input
                        id="tool-name"
                        value={name}
                        onChange={(e) => onNameChange(e.target.value)}
                        placeholder="Tool name"
                    />
                </div>

                {/* Tool Description */}
                <div className="space-y-2">
                    <Label htmlFor="tool-description">Description</Label>
                    <p className="text-xs text-muted-foreground">
                        Provide a description which makes it easy for LLM to understand what this tool does
                    </p>
                    <Textarea
                        id="tool-description"
                        value={description}
                        onChange={(e) => onDescriptionChange(e.target.value)}
                        placeholder="Describe what this tool does..."
                        rows={3}
                    />
                </div>
            </CardContent>
        </Card>
    );
}
