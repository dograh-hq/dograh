import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface DictionaryDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    dictionary: string;
    onSave: (dictionary: string) => Promise<void>;
}

export const DictionaryDialog = ({
    open,
    onOpenChange,
    dictionary,
    onSave
}: DictionaryDialogProps) => {
    const [dictionaryValue, setDictionaryValue] = useState(dictionary);

    // Sync local state with prop when dialog opens
    useEffect(() => {
        if (open) {
            setDictionaryValue(dictionary);
        }
    }, [open, dictionary]);

    const handleSave = async () => {
        await onSave(dictionaryValue);
        onOpenChange(false);
    };

    const handleDialogOpenChange = (isOpen: boolean) => {
        onOpenChange(isOpen);
        if (isOpen) {
            setDictionaryValue(dictionary);
        }
    };

    return (
        <Dialog open={open} onOpenChange={handleDialogOpenChange}>
            <DialogContent className="max-w-lg">
                <DialogHeader>
                    <DialogTitle>Dictionary</DialogTitle>
                    <DialogDescription>
                    Add any specific words that you would want the bot to actively listen for. The Voice Agent learns your
                    unique words and names. Add expected words and phrases, company jargon, named entities, or industry-specific lingo. <br/>
                    Example: billing department, tretinoin etc. <br/>
                    (May incur extra cost depending on provider)
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-4">
                    <div className="space-y-2">
                        <Label htmlFor="dictionary" className="text-sm font-medium">Words</Label>
                        <Textarea
                            id="dictionary"
                            placeholder="Enter words separated by comma"
                            value={dictionaryValue}
                            onChange={(e) => setDictionaryValue(e.target.value)}
                            rows={4}
                            className="resize-none"
                        />
                    </div>
                </div>
                <DialogFooter>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" onClick={() => onOpenChange(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleSave}>
                            Save Dictionary
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
