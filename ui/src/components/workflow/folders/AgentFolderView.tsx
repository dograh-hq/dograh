'use client';

import type { FolderResponse, WorkflowListResponse } from '@/client/types.gen';

import { WorkflowTable } from '../WorkflowTable';
import { FolderSection } from './FolderSection';

interface AgentFolderViewProps {
    /** Active (non-archived) agents only. */
    workflows: WorkflowListResponse[];
    folders: FolderResponse[];
}

/**
 * Renders active agents grouped into collapsible folder sections.
 *
 * When the organization has no folders yet, this falls back to the original
 * flat table so the feature stays invisible until someone creates a folder.
 */
export function AgentFolderView({ workflows, folders }: AgentFolderViewProps) {
    // No folders → keep the original flat list (no folder chrome, nowhere to move to).
    if (folders.length === 0) {
        return <WorkflowTable workflows={workflows} showArchived={false} />;
    }

    // Group agents by folder. Agents whose folder_id is null — or points at a
    // folder we didn't get back — fall into "Uncategorized".
    const folderIds = new Set(folders.map((f) => f.id));
    const byFolder = new Map<number, WorkflowListResponse[]>();
    const uncategorized: WorkflowListResponse[] = [];

    for (const wf of workflows) {
        if (wf.folder_id != null && folderIds.has(wf.folder_id)) {
            const bucket = byFolder.get(wf.folder_id) ?? [];
            bucket.push(wf);
            byFolder.set(wf.folder_id, bucket);
        } else {
            uncategorized.push(wf);
        }
    }

    return (
        <div className="space-y-1">
            {folders.map((folder) => (
                <FolderSection
                    key={folder.id}
                    kind="folder"
                    folder={folder}
                    workflows={byFolder.get(folder.id) ?? []}
                    allFolders={folders}
                    defaultOpen={false}
                />
            ))}
            {uncategorized.length > 0 && (
                <FolderSection
                    kind="uncategorized"
                    workflows={uncategorized}
                    allFolders={folders}
                />
            )}
        </div>
    );
}
