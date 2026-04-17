import { PlusIcon, Trash2Icon } from "lucide-react";

import { ExtractionVariable } from "@/components/flow/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

interface ExtractionPanelProps {
    enabled: boolean;
    setEnabled: (value: boolean) => void;
    prompt: string;
    setPrompt: (value: string) => void;
    variables: ExtractionVariable[];
    setVariables: (vars: ExtractionVariable[]) => void;
}

export const ExtractionPanel = ({
    enabled,
    setEnabled,
    prompt,
    setPrompt,
    variables,
    setVariables,
}: ExtractionPanelProps) => {
    const handleVariableNameChange = (idx: number, value: string) => {
        const next = [...variables];
        next[idx] = { ...next[idx], name: value };
        setVariables(next);
    };

    const handleVariableTypeChange = (
        idx: number,
        value: 'string' | 'number' | 'boolean',
    ) => {
        const next = [...variables];
        next[idx] = { ...next[idx], type: value };
        setVariables(next);
    };

    const handleVariablePromptChange = (idx: number, value: string) => {
        const next = [...variables];
        next[idx] = { ...next[idx], prompt: value };
        setVariables(next);
    };

    const handleRemoveVariable = (idx: number) => {
        setVariables(variables.filter((_, i) => i !== idx));
    };

    const handleAddVariable = () => {
        setVariables([...variables, { name: '', type: 'string', prompt: '' }]);
    };

    return (
        <>
            <div className="flex items-center space-x-2 pt-2">
                <Switch id="enable-extraction" checked={enabled} onCheckedChange={setEnabled} />
                <Label htmlFor="enable-extraction">Enable Variable Extraction</Label>
                <Label className="text-xs text-muted-foreground ml-2">
                    Are there any variables you would like to extract from the conversation?
                </Label>
            </div>

            {enabled && (
                <div className="border rounded-md p-3 mt-2 space-y-2 bg-muted/20">
                    <Label>Extraction Prompt</Label>
                    <Label className="text-xs text-muted-foreground">
                        Provide an overall extraction prompt that guides how variables should be extracted from the conversation.
                    </Label>
                    <Textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        className="min-h-[80px] max-h-[200px] resize-none"
                        style={{ overflowY: 'auto' }}
                    />

                    <Label>Variables</Label>
                    <Label className="text-xs text-muted-foreground">
                        Define each variable you want to extract along with its data type.
                    </Label>

                    {variables.map((v, idx) => (
                        <div key={idx} className="space-y-2 border rounded-md p-2 bg-background">
                            <div className="flex items-center gap-2">
                                <Input
                                    placeholder="Variable name"
                                    value={v.name}
                                    onChange={(e) => handleVariableNameChange(idx, e.target.value)}
                                />
                                <select
                                    className="border rounded-md p-2 text-sm bg-background"
                                    value={v.type}
                                    onChange={(e) =>
                                        handleVariableTypeChange(
                                            idx,
                                            e.target.value as 'string' | 'number' | 'boolean',
                                        )
                                    }
                                >
                                    <option value="string">String</option>
                                    <option value="number">Number</option>
                                    <option value="boolean">Boolean</option>
                                </select>
                                <Button variant="outline" size="icon" onClick={() => handleRemoveVariable(idx)}>
                                    <Trash2Icon className="w-4 h-4" />
                                </Button>
                            </div>
                            <Textarea
                                placeholder="Extraction prompt for this variable"
                                value={v.prompt ?? ''}
                                onChange={(e) => handleVariablePromptChange(idx, e.target.value)}
                                className="min-h-[60px] resize-none"
                            />
                        </div>
                    ))}

                    <Button variant="outline" size="sm" className="w-fit" onClick={handleAddVariable}>
                        <PlusIcon className="w-4 h-4 mr-1" /> Add Variable
                    </Button>
                </div>
            )}
        </>
    );
};
