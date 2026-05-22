'use client';

import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface FolderFormDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    /** Pre-fill the input (used when renaming). */
    initialName?: string;
    submitLabel: string;
    /** Resolve to close the dialog; reject/throw to keep it open (e.g. on error). */
    onSubmit: (name: string) => Promise<void>;
}

/**
 * Shared single-field dialog used for both creating and renaming a folder.
 * Keeps name validation and the pending state in one place.
 */
export function FolderFormDialog({
    open,
    onOpenChange,
    title,
    initialName = '',
    submitLabel,
    onSubmit,
}: FolderFormDialogProps) {
    const [name, setName] = useState(initialName);
    const [isSubmitting, setIsSubmitting] = useState(false);

    // Reset the field whenever the dialog (re)opens.
    useEffect(() => {
        if (open) setName(initialName);
    }, [open, initialName]);

    const trimmed = name.trim();
    const canSubmit = trimmed.length > 0 && trimmed !== initialName.trim() && !isSubmitting;

    const handleSubmit = async () => {
        if (!canSubmit) return;
        setIsSubmitting(true);
        try {
            await onSubmit(trimmed);
            onOpenChange(false);
        } catch {
            // onSubmit surfaces its own error toast; keep the dialog open.
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                </DialogHeader>
                <div className="space-y-2 py-2">
                    <Label htmlFor="folder-name">Folder name</Label>
                    <Input
                        id="folder-name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="e.g. Sales, Support, Onboarding"
                        maxLength={100}
                        autoFocus
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                                e.preventDefault();
                                handleSubmit();
                            }
                        }}
                    />
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={handleSubmit} disabled={!canSubmit}>
                        {isSubmitting ? 'Saving...' : submitLabel}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
