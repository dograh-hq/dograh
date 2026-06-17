'use client';

import {
    Archive,
    ChevronRight,
    Folder as FolderIcon,
    FolderOpen,
    Inbox,
    MoreVertical,
    Pencil,
    Trash2,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import {
    deleteFolderApiV1FolderFolderIdDelete,
    renameFolderApiV1FolderFolderIdPut,
} from '@/client/sdk.gen';
import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import logger from '@/lib/logger';
import { cn } from '@/lib/utils';

import { WorkflowTable } from '../WorkflowTable';
import { FolderFormDialog } from './FolderFormDialog';

/**
 * - `folder`        — a real, renameable/deletable folder of active agents
 * - `uncategorized` — active agents with no folder
 * - `archived`      — archived agents (restore-only; not a move target)
 */
type SectionKind = 'folder' | 'uncategorized' | 'archived';

interface FolderSectionProps {
    kind: SectionKind;
    /** Required when kind === 'folder'; ignored otherwise. */
    folder?: FolderResponse | null;
    workflows: WorkflowListResponse[];
    /** All folders, passed through so each row's "Move to folder" menu has targets. */
    allFolders?: FolderResponse[];
    /** Defaults to open only for Uncategorized; folders and Archived start collapsed. */
    defaultOpen?: boolean;
}

export function FolderSection({
    kind,
    folder = null,
    workflows,
    allFolders = [],
    defaultOpen,
}: FolderSectionProps) {
    const router = useRouter();
    const [open, setOpen] = useState(defaultOpen ?? kind === 'uncategorized');
    const [isRenaming, setIsRenaming] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);

    const isFolder = kind === 'folder';
    const isArchived = kind === 'archived';
    const count = workflows.length;
    const title = isFolder ? (folder?.name ?? '') : isArchived ? 'Archived' : 'Uncategorized';

    const handleRename = async (name: string) => {
        if (!folder) return;
        const response = await renameFolderApiV1FolderFolderIdPut({
            path: { folder_id: folder.id },
            body: { name },
        });
        if (response.error) {
            const detail =
                (response.error as { detail?: string })?.detail ??
                'Failed to rename folder';
            toast.error(detail);
            throw new Error(detail);
        }
        toast.success('Folder renamed');
        router.refresh();
    };

    const handleDelete = async () => {
        if (!folder) return;
        setIsDeleting(true);
        try {
            const response = await deleteFolderApiV1FolderFolderIdDelete({
                path: { folder_id: folder.id },
            });
            if (response.error) {
                throw new Error('Failed to delete folder');
            }
            toast.success(`Folder "${folder.name}" deleted`);
            setConfirmDelete(false);
            router.refresh();
        } catch (err) {
            logger.error(`Error deleting folder: ${err}`);
            toast.error('Failed to delete folder');
        } finally {
            setIsDeleting(false);
        }
    };

    return (
        <div className="mb-3">
            <Collapsible open={open} onOpenChange={setOpen}>
                <div className="flex items-center gap-1">
                    <CollapsibleTrigger asChild>
                        <button
                            className="group flex flex-1 items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent"
                            aria-label={`Toggle ${title}`}
                        >
                            <ChevronRight
                                size={16}
                                className={cn(
                                    'shrink-0 text-muted-foreground transition-transform duration-200',
                                    open && 'rotate-90',
                                )}
                            />
                            {isFolder ? (
                                open ? (
                                    <FolderOpen size={17} className="shrink-0 text-amber-500" />
                                ) : (
                                    <FolderIcon size={17} className="shrink-0 text-amber-500" />
                                )
                            ) : isArchived ? (
                                <Archive size={16} className="shrink-0 text-muted-foreground" />
                            ) : (
                                <Inbox size={17} className="shrink-0 text-muted-foreground" />
                            )}
                            <span
                                className={cn('font-medium', !isFolder && 'text-muted-foreground')}
                            >
                                {title}
                            </span>
                            <Badge variant="secondary" className="ml-1 font-normal">
                                {count}
                            </Badge>
                        </button>
                    </CollapsibleTrigger>

                    {isFolder && (
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 text-muted-foreground"
                                    aria-label="Folder actions"
                                >
                                    <MoreVertical size={16} />
                                </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => setIsRenaming(true)}>
                                    <Pencil size={14} className="mr-2" />
                                    Rename
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                    onClick={() => setConfirmDelete(true)}
                                    className="text-destructive focus:text-destructive"
                                >
                                    <Trash2 size={14} className="mr-2" />
                                    Delete
                                </DropdownMenuItem>
                            </DropdownMenuContent>
                        </DropdownMenu>
                    )}
                </div>

                <CollapsibleContent className="data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-1">
                    <div className="pl-7 pt-2">
                        {count > 0 ? (
                            <WorkflowTable
                                workflows={workflows}
                                showArchived={isArchived}
                                // Archived agents are restore-only — not a move target.
                                folders={isArchived ? undefined : allFolders}
                                currentFolderId={folder?.id ?? null}
                            />
                        ) : (
                            <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
                                {isArchived
                                    ? 'No archived agents.'
                                    : isFolder
                                      ? 'This folder is empty. Use “Move to folder” on an agent to add it here.'
                                      : 'No uncategorized agents.'}
                            </div>
                        )}
                    </div>
                </CollapsibleContent>
            </Collapsible>

            {isFolder && folder && (
                <>
                    <FolderFormDialog
                        open={isRenaming}
                        onOpenChange={setIsRenaming}
                        title="Rename folder"
                        initialName={folder.name}
                        submitLabel="Rename"
                        onSubmit={handleRename}
                    />
                    <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
                        <AlertDialogContent>
                            <AlertDialogHeader>
                                <AlertDialogTitle>Delete “{folder.name}”?</AlertDialogTitle>
                                <AlertDialogDescription>
                                    The {count} agent{count === 1 ? '' : 's'} in this folder
                                    won’t be deleted - they’ll move to Uncategorized.
                                </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                                <AlertDialogCancel disabled={isDeleting}>
                                    Cancel
                                </AlertDialogCancel>
                                <AlertDialogAction
                                    onClick={(e) => {
                                        e.preventDefault();
                                        handleDelete();
                                    }}
                                    disabled={isDeleting}
                                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                >
                                    {isDeleting ? 'Deleting...' : 'Delete folder'}
                                </AlertDialogAction>
                            </AlertDialogFooter>
                        </AlertDialogContent>
                    </AlertDialog>
                </>
            )}
        </div>
    );
}
