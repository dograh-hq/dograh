import { Trash2Icon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface TemplateContextVariablesDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    templateContextVariables: Record<string, string>;
    onSave: (variables: Record<string, string>) => Promise<void>;
}

export const TemplateContextVariablesDialog = ({
    open,
    onOpenChange,
    templateContextVariables,
    onSave
}: TemplateContextVariablesDialogProps) => {
    const [contextVars, setContextVars] = useState<Record<string, string>>(templateContextVariables);
    const [newKey, setNewKey] = useState("");
    const [newValue, setNewValue] = useState("");

    const handleAddContextVar = () => {
        if (newKey && newValue) {
            setContextVars(prev => ({ ...prev, [newKey]: newValue }));
        }
        setNewKey("");
        setNewValue("");
    };

    const handleRemoveContextVar = (key: string) => {
        setContextVars(prev => {
            const newVars = { ...prev };
            delete newVars[key];
            return newVars;
        });
    };

    const handleSave = async () => {
        let varsToSave = contextVars;
        // Include any newly typed key/value that hasn't been added via the "Add Variable" button
        if (newKey && newValue) {
            varsToSave = { ...varsToSave, [newKey]: newValue };
        }
        await onSave(varsToSave);
        onOpenChange(false);
    };

    const handleDialogOpenChange = (isOpen: boolean) => {
        onOpenChange(isOpen);
        if (isOpen) {
            setContextVars(templateContextVariables);
            setNewKey("");
            setNewValue("");
        }
    };

    return (
        <Dialog open={open} onOpenChange={handleDialogOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Template Context Variables</DialogTitle>
                    <DialogDescription>
                        Add or remove template context variables that will be available to your workflow. You can use
                        these variables within your workflow nodes within double curly braces. Example: {`{{variable_name}}`}.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4">
                    {/* Existing Variables */}
                    {Object.entries(contextVars).length > 0 && (
                        <div className="space-y-2">
                            <Label className="text-sm font-medium">Current Variables</Label>
                            {Object.entries(contextVars).map(([key, value]) => (
                                <div key={key} className="flex items-center gap-2 p-2 border rounded-md">
                                    <div className="flex-1">
                                        <div className="text-sm font-medium">{key}</div>
                                        <div className="text-xs text-gray-500 truncate">{value}</div>
                                    </div>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        onClick={() => handleRemoveContextVar(key)}
                                    >
                                        <Trash2Icon className="w-4 h-4" />
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Add New Variable */}
                    <div className="space-y-3">
                        <Label className="text-sm font-medium">Add New Variable</Label>
                        <div className="space-y-2">
                            <div className="flex gap-2">
                                <div className="flex-1">
                                    <Label htmlFor="key" className="text-xs">Key</Label>
                                    <Input
                                        id="key"
                                        placeholder="Enter variable key"
                                        value={newKey}
                                        onChange={(e) => setNewKey(e.target.value)}
                                    />
                                </div>
                                <div className="flex-1">
                                    <Label htmlFor="value" className="text-xs">Value</Label>
                                    <Input
                                        id="value"
                                        placeholder="Enter variable value"
                                        value={newValue}
                                        onChange={(e) => setNewValue(e.target.value)}
                                    />
                                </div>
                            </div>
                            <Button
                                size="sm"
                                onClick={handleAddContextVar}
                                disabled={!newKey || !newValue}
                            >
                                Add Variable
                            </Button>
                        </div>
                    </div>
                </div>
                <DialogFooter>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" onClick={() => onOpenChange(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave}>
                            Save Variables
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
