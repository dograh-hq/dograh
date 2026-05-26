'use client';

import { FolderPlus } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { createFolderApiV1FolderPost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';

import { FolderFormDialog } from './FolderFormDialog';

export function CreateFolderButton() {
    const router = useRouter();
    const [isOpen, setIsOpen] = useState(false);

    const handleCreate = async (name: string) => {
        const response = await createFolderApiV1FolderPost({ body: { name } });
        if (response.error) {
            // 409 = duplicate name; surface the server's message when present.
            const detail =
                (response.error as { detail?: string })?.detail ??
                'Failed to create folder';
            toast.error(detail);
            throw new Error(detail);
        }
        toast.success(`Folder "${name}" created`);
        router.refresh();
    };

    return (
        <>
            <Button variant="outline" onClick={() => setIsOpen(true)}>
                <FolderPlus className="w-4 h-4 mr-2" />
                New Folder
            </Button>
            <FolderFormDialog
                open={isOpen}
                onOpenChange={setIsOpen}
                title="Create folder"
                submitLabel="Create"
                onSubmit={handleCreate}
            />
        </>
    );
}
