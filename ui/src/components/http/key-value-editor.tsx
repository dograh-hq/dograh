"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface KeyValueItem {
    key: string;
    value: string;
}

interface KeyValueEditorProps {
    items: KeyValueItem[];
    onChange: (items: KeyValueItem[]) => void;
    keyPlaceholder?: string;
    valuePlaceholder?: string;
    addButtonText?: string;
    emptyMessage?: string;
    disabled?: boolean;
}

export function KeyValueEditor({
    items,
    onChange,
    keyPlaceholder = "Key",
    valuePlaceholder = "Value",
    addButtonText = "Add",
    disabled = false,
}: KeyValueEditorProps) {
    const addItem = () => {
        onChange([...items, { key: "", value: "" }]);
    };

    const updateItem = (index: number, field: "key" | "value", value: string) => {
        const newItems = [...items];
        newItems[index] = { ...newItems[index], [field]: value };
        onChange(newItems);
    };

    const removeItem = (index: number) => {
        onChange(items.filter((_, i) => i !== index));
    };

    return (
        <div className="space-y-2">
            {items.map((item, index) => (
                <div key={index} className="flex items-center gap-2">
                    <Input
                        placeholder={keyPlaceholder}
                        value={item.key}
                        onChange={(e) => updateItem(index, "key", e.target.value)}
                        className="flex-1"
                        disabled={disabled}
                    />
                    <Input
                        placeholder={valuePlaceholder}
                        value={item.value}
                        onChange={(e) => updateItem(index, "value", e.target.value)}
                        className="flex-1"
                        disabled={disabled}
                    />
                    <Button
                        variant="outline"
                        size="icon"
                        onClick={() => removeItem(index)}
                        disabled={disabled}
                    >
                        <Trash2Icon className="h-4 w-4" />
                    </Button>
                </div>
            ))}

            <Button
                variant="outline"
                size="sm"
                onClick={addItem}
                className="w-fit"
                disabled={disabled}
            >
                <PlusIcon className="h-4 w-4 mr-1" /> {addButtonText}
            </Button>
        </div>
    );
}
