'use client';

import { Archive, Eye, RotateCcw } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';
import { toast } from 'sonner';

import { updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useUserConfig } from '@/context/UserConfigContext';

interface Workflow {
    id: number;
    name: string;
    status: string;
    created_at: string;
    total_runs?: number | null;
}

interface WorkflowTableProps {
    workflows: Workflow[];
    showArchived: boolean;
}

export function WorkflowTable({ workflows, showArchived }: WorkflowTableProps) {
    const router = useRouter();
    const { accessToken } = useUserConfig();
    const [isPending, startTransition] = useTransition();
    const [loadingWorkflowId, setLoadingWorkflowId] = useState<number | null>(null);

    const handleView = (id: number) => {
        router.push(`/workflow/${id}`);
    };

    const handleArchiveToggle = async (id: number, currentStatus: string) => {
        if (!accessToken) {
            toast.error('Authentication required');
            return;
        }

        const newStatus = currentStatus === 'active' ? 'archived' : 'active';
        const action = currentStatus === 'active' ? 'Archive' : 'Restore';

        setLoadingWorkflowId(id);

        try {
            const response = await updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut({
                path: {
                    workflow_id: id,
                },
                body: {
                    status: newStatus,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                toast.success(`Workflow ${action.toLowerCase()}d successfully`);
                startTransition(() => {
                    router.refresh();
                });
            }
        } catch (error) {
            console.error(`Error ${action.toLowerCase()}ing workflow:`, error);
            toast.error(`Failed to ${action.toLowerCase()} workflow`);
        } finally {
            setLoadingWorkflowId(null);
        }
    };

    return (
        <div className="bg-card border rounded-lg overflow-hidden shadow-sm">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead className="font-semibold">Agent Name</TableHead>
                        <TableHead className="font-semibold">Created At</TableHead>
                        <TableHead className="font-semibold text-center">Total Runs</TableHead>
                        <TableHead className="font-semibold text-right">Actions</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {workflows.map((workflow) => (
                        <TableRow
                            key={workflow.id}
                            className={`hover:bg-accent transition-colors ${showArchived ? 'opacity-60' : ''}`}
                        >
                            <TableCell className="font-medium">
                                {workflow.name}
                            </TableCell>
                            <TableCell>
                                {new Date(workflow.created_at).toLocaleDateString('en-US', {
                                    year: 'numeric',
                                    month: 'short',
                                    day: 'numeric',
                                })}
                            </TableCell>
                            <TableCell className="text-center">
                                <span className="inline-flex items-center justify-center min-w-[2rem] px-2 py-1 text-sm font-semibold bg-muted rounded-full">
                                    {workflow.total_runs || 0}
                                </span>
                            </TableCell>
                            <TableCell className="text-right">
                                <div className="flex justify-end gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handleView(workflow.id)}
                                        className="flex items-center gap-2"
                                    >
                                        <Eye size={16} />
                                        View
                                    </Button>
                                    <Button
                                        variant={showArchived ? "default" : "outline"}
                                        size="sm"
                                        onClick={() => handleArchiveToggle(workflow.id, workflow.status)}
                                        disabled={loadingWorkflowId === workflow.id || isPending}
                                        className="flex items-center gap-2"
                                    >
                                        {loadingWorkflowId === workflow.id ? (
                                            <>
                                                <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                                {showArchived ? 'Restoring...' : 'Archiving...'}
                                            </>
                                        ) : (
                                            <>
                                                {showArchived ? (
                                                    <>
                                                        <RotateCcw size={16} />
                                                        Restore
                                                    </>
                                                ) : (
                                                    <>
                                                        <Archive size={16} />
                                                        Archive
                                                    </>
                                                )}
                                            </>
                                        )}
                                    </Button>
                                </div>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </div>
    );
}
