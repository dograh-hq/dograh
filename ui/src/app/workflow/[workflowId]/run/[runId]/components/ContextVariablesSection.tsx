import { Trash2Icon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface ContextVariablesSectionProps {
    initialContext: Record<string, string>;
    setInitialContext: (variables: Record<string, string>) => void;
    disabled?: boolean;
}

export const ContextVariablesSection = ({
    initialContext,
    setInitialContext,
    disabled = false
}: ContextVariablesSectionProps) => {
    const [newKey, setNewKey] = useState("");
    const [newValue, setNewValue] = useState("");

    const handleAddContextVar = () => {
        if (newKey && newValue && !initialContext[newKey]) {
            setInitialContext({ ...initialContext, [newKey]: newValue });
            setNewKey("");
            setNewValue("");
        }
    };

    const handleRemoveContextVar = (key: string) => {
        const newVars = { ...initialContext };
        delete newVars[key];
        setInitialContext(newVars);
    };

    const handleUpdateContextVar = (key: string, value: string) => {
        setInitialContext({ ...initialContext, [key]: value });
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-lg">Template Context Variables</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Existing Variables */}
                {Object.entries(initialContext).length > 0 && (
                    <div className="space-y-2">
                        <Label className="text-sm font-medium">Current Variables</Label>
                        {Object.entries(initialContext).map(([key, value]) => (
                            <div key={key} className="flex items-center gap-2 p-3 border rounded-md bg-muted">
                                <div className="flex-1">
                                    <Label className="text-xs text-muted-foreground">{key}</Label>
                                    <Input
                                        value={value}
                                        onChange={(e) => handleUpdateContextVar(key, e.target.value)}
                                        disabled={disabled}
                                        className="mt-1"
                                    />
                                </div>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => handleRemoveContextVar(key)}
                                    disabled={disabled}
                                >
                                    <Trash2Icon className="w-4 h-4 text-red-500" />
                                </Button>
                            </div>
                        ))}
                    </div>
                )}

                {/* Add New Variable */}
                <div className="space-y-3">
                    <Label className="text-sm font-medium">Add New Variable</Label>
                    <div className="flex gap-2">
                        <div className="flex-1">
                            <Input
                                placeholder="Variable key"
                                value={newKey}
                                onChange={(e) => setNewKey(e.target.value)}
                                disabled={disabled}
                            />
                        </div>
                        <div className="flex-1">
                            <Input
                                placeholder="Variable value"
                                value={newValue}
                                onChange={(e) => setNewValue(e.target.value)}
                                disabled={disabled}
                            />
                        </div>
                        <Button
                            onClick={handleAddContextVar}
                            disabled={!newKey || !newValue || disabled || !!initialContext[newKey]}
                        >
                            Add
                        </Button>
                    </div>
                    {newKey && initialContext[newKey] && (
                        <p className="text-sm text-red-500">Variable with this key already exists</p>
                    )}
                </div>
            </CardContent>
        </Card>
    );
};
