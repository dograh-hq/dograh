import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath, useReactFlow } from '@xyflow/react';
import { AlertCircle, Pencil } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
import { useWorkflowStore } from "@/app/workflow/[workflowId]/stores/workflowStore";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from '@/components/ui/textarea';
import { cn } from "@/lib/utils";

import { FlowEdge, FlowEdgeData, FlowNode } from '../types';
type CustomEdge = Edge<{ value: number }, 'custom'>;


interface EdgeDetailsDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    data?: FlowEdgeData;
    onSave: (value: FlowEdgeData) => void;
}

const EdgeDetailsDialog = ({ open, onOpenChange, data, onSave }: EdgeDetailsDialogProps) => {
    const [condition, setCondition] = useState(data?.condition ?? '');
    const [label, setLabel] = useState(data?.label ?? '');

    // Update form state when data changes (e.g., from undo/redo)
    useEffect(() => {
        if (open) {
            setCondition(data?.condition ?? '');
            setLabel(data?.label ?? '');
        }
    }, [data, open]);

    const handleSave = () => {
        onSave({ condition: condition, label: label });
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Edit Condition</DialogTitle>
                    {data?.invalid && data.validationMessage && (
                        <div className="mt-2 flex items-center gap-2 rounded-md bg-red-50 p-2 text-sm text-red-500 border border-red-200">
                            <AlertCircle className="h-4 w-4" />
                            <span>{data.validationMessage}</span>
                        </div>
                    )}
                </DialogHeader>
                <div className="grid gap-4 py-4">
                    <div className="grid gap-2">
                        <Label>Condition Label</Label>
                        <Label className="text-xs text-gray-500">
                            Enter a short label which helps identify this pathway in logs
                        </Label>
                        <Input
                            type="text"
                            value={label}
                            maxLength={64}
                            onChange={(e) => setLabel(e.target.value)}
                        />
                        <div className="text-xs text-gray-500">
                            {label.length}/64 characters
                        </div>
                    </div>
                    <div className="grid gap-2">
                        <Label>Condition</Label>
                        <Label className="text-xs text-gray-500">
                            Describe a condition that will be evaluated to determine if this pathway should be taken
                        </Label>
                        <Textarea
                            value={condition}
                            onChange={(e) => setCondition(e.target.value)}
                        />
                    </div>
                </div>
                <DialogFooter>
                    <div className="flex items-center gap-2">
                        <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
                        <Button onClick={handleSave}>Save</Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};

interface CustomEdgeProps extends EdgeProps {
    data: FlowEdgeData;
}

export default function CustomEdge(props: CustomEdgeProps) {
    const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, style, selected } = props;

    const { getEdges, setNodes } = useReactFlow<FlowNode, FlowEdge>();
    const { saveWorkflow } = useWorkflow();
    const updateEdge = useWorkflowStore((state) => state.updateEdge);
    const [open, setOpen] = useState(false);
    const [isHovered, setIsHovered] = useState(false);

    const parallel = getEdges().filter(
        (e) =>
            (e.source === source && e.target === target) ||
            (e.source === target && e.target === source)
    );

    // 2) if there are two, sort by id and pick an index
    let offsetX = 0;
    let offsetY = 0;
    if (parallel.length > 1) {
        const sorted = parallel.slice().sort((a, b) => a.id.localeCompare(b.id));
        const idx = sorted.findIndex((e) => e.id === id);

        // first edge (idx 0) moves right & down;
        // second edge (idx 1) moves left & up
        if (idx === 0) {
            offsetX = 100;
            offsetY = 0;
        } else {
            offsetX = 0;
            offsetY = -50;
        }
    }

    // 3) draw the bezier path + get label coords
    const [edgePath, labelX, labelY] = getBezierPath({
        sourceX,
        sourceY,
        sourcePosition,
        targetX,
        targetY,
        targetPosition,
    });

    // Update connected nodes when edge is selected or hovered
    useEffect(() => {
        setNodes((nodes) => {
            return nodes.map((node) => {
                if (node.id === source || node.id === target) {
                    // Update both properties based on edge state
                    const shouldSelectThroughEdge = selected || false;
                    const shouldHoverThroughEdge = isHovered || false;

                    // Only update if state actually changed
                    if (
                        node.data.selected_through_edge !== shouldSelectThroughEdge ||
                        node.data.hovered_through_edge !== shouldHoverThroughEdge
                    ) {
                        return {
                            ...node,
                            data: {
                                ...node.data,
                                selected_through_edge: shouldSelectThroughEdge,
                                hovered_through_edge: shouldHoverThroughEdge
                            }
                        };
                    }
                }
                return node;
            });
        });
    }, [selected, isHovered, source, target, setNodes]);

    const handleSaveEdgeData = useCallback(async (updatedData: FlowEdgeData) => {
        // Use the workflow store's updateEdge method to properly track history
        updateEdge(id, { data: updatedData });

        // Save the workflow after updating edge data with a small delay to ensure state is updated
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    }, [id, updateEdge, saveWorkflow]);

    return (
        <>
            <g
                onMouseEnter={() => setIsHovered(true)}
                onMouseLeave={() => setIsHovered(false)}
                onDoubleClick={() => setOpen(true)}
            >
                <BaseEdge
                    id={id}
                    path={edgePath}
                    style={{
                        ...style,
                        stroke: selected
                            ? '#3B82F6'  // blue-500 when selected
                            : isHovered
                                ? '#60A5FA'  // blue-400 when hovered
                                : data?.invalid ? '#EF4444' : '#94A3B8',
                        strokeWidth: selected ? 4 : isHovered ? 3 : 2.5,
                        filter: selected
                            ? 'drop-shadow(0 0 8px rgba(59, 130, 246, 0.6))'
                            : isHovered
                                ? 'drop-shadow(0 0 6px rgba(96, 165, 250, 0.4))'
                                : 'none',
                        transition: 'all 0.2s ease',
                    }}
                    interactionWidth={20}
                />
            </g>
            {/* Always show label, expand on select/hover */}
            <EdgeLabelRenderer>
                <div
                    style={{
                        position: 'absolute',
                        pointerEvents: 'all',
                        transformOrigin: 'center',
                        transform: `translate(-50%, -50%) translate(${labelX + offsetX}px, ${labelY + offsetY}px)`,
                        zIndex: 1000,
                    }}
                    className="nodrag nopan"
                    onMouseEnter={() => setIsHovered(true)}
                    onMouseLeave={() => setIsHovered(false)}
                    onDoubleClick={() => setOpen(true)}
                >
                    {/* Show full EdgeLabel when selected or hovered, otherwise show simple label */}
                    {(selected || isHovered) ? (
                        <div className={cn(
                            "flex flex-col gap-2 bg-white rounded-lg border-2 shadow-xl min-w-[200px]",
                            "animate-in fade-in zoom-in duration-200",
                            data?.invalid ? "border-red-500 shadow-[0_0_15px_rgba(239,68,68,0.5)]" : "border-gray-300"
                        )}>
                            {/* Header with label */}
                            <div className={cn(
                                "flex items-center justify-between px-3 py-2 border-b",
                                data?.invalid ? "bg-red-50 border-red-200" : "bg-gray-50 border-gray-200"
                            )}>
                                <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
                                    Condition - EdgeID: {id}
                                </span>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-6 w-6 p-0 hover:bg-gray-200"
                                    onClick={() => setOpen(true)}
                                >
                                    <Pencil className="h-3 w-3" />
                                </Button>
                            </div>
                            {/* Content */}
                            <div className="px-3 pb-3">
                                <div className="text-sm font-medium text-gray-900 break-words">
                                    {data?.label || data?.condition || 'Click to set condition'}
                                </div>
                            </div>
                        </div>
                    ) : (
                        /* Simple label shown by default */
                        <div className={cn(
                            "px-2 py-1 bg-white rounded border shadow-sm",
                            data?.invalid ? "border-red-400 text-red-600" : "border-gray-300 text-gray-700"
                        )}>
                            <div className="text-xs font-medium">
                                {data?.label || data?.condition || 'No condition'}
                            </div>
                        </div>
                    )}
                </div>
            </EdgeLabelRenderer>
            <EdgeDetailsDialog
                open={open}
                onOpenChange={setOpen}
                data={data}
                onSave={handleSaveEdgeData}
            />
        </>
    );
}
