import { BaseEdge, type Edge, EdgeLabelRenderer, type EdgeProps, getBezierPath, useReactFlow } from '@xyflow/react';
import { AlertCircle, Pencil } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { useWorkflow } from "@/app/workflow/[workflowId]/contexts/WorkflowContext";
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

    const { getEdges, setEdges, setNodes } = useReactFlow<FlowNode, FlowEdge>();
    const { saveWorkflow } = useWorkflow();
    const [open, setOpen] = useState(false);
    const [isHovered, setIsHovered] = useState(false);

    // Edge is highlighted when either selected or hovered
    const isHighlighted = selected || isHovered;

    console.log("in CustomEdge", id, selected, isHovered, isHighlighted);

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

    // Highlight connected nodes when edge is highlighted (selected or hovered)
    useEffect(() => {
        if (isHighlighted) {
            setNodes((nodes) =>
                nodes.map((node) => {
                    if (node.id === source || node.id === target) {
                        return {
                            ...node,
                            data: { ...node.data, highlighted: true }
                        };
                    }
                    return node;
                })
            );
        } else {
            setNodes((nodes) =>
                nodes.map((node) => {
                    if (node.id === source || node.id === target) {
                        return {
                            ...node,
                            data: { ...node.data, highlighted: false }
                        };
                    }
                    return node;
                })
            );
        }
    }, [isHighlighted, source, target, setNodes, selected]);

    const handleSaveEdgeData = useCallback(async (updatedData: FlowEdgeData) => {
        // Update the node data in the ReactFlow nodes state
        setEdges((edges) => {
            const updatedEdges = edges.map((edge) =>
                edge.id === id
                    ? { ...edge, data: updatedData }
                    : edge
            )
            return updatedEdges;
        }
        );
        // Save the workflow after updating edge data with a small delay to ensure state is updated
        setTimeout(async () => {
            await saveWorkflow();
        }, 100);
    }, [id, setEdges, saveWorkflow]);

    return (
        <>
            <g
                onMouseEnter={() => setIsHovered(true)}
                onMouseLeave={() => setIsHovered(false)}
            >
                <BaseEdge
                    id={id}
                    path={edgePath}
                    style={{
                        ...style,
                        stroke: isHighlighted
                            ? '#3B82F6'  // blue-500 when highlighted (selected or hovered)
                            : data?.invalid ? '#EF4444' : '#94A3B8',
                        strokeWidth: isHighlighted ? 4 : 2.5,
                        filter: isHighlighted ? 'drop-shadow(0 0 8px rgba(59, 130, 246, 0.6))' : 'none',
                        transition: 'all 0.2s ease',
                    }}
                    interactionWidth={20}
                />
            </g>
            {/* Show label when highlighted (selected or hovered), positioned at edge center */}
            {isHighlighted && (
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
                    >
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
                                    Condition
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
                    </div>
                </EdgeLabelRenderer>
            )}
            <EdgeDetailsDialog
                open={open}
                onOpenChange={setOpen}
                data={data}
                onSave={handleSaveEdgeData}
            />
        </>
    );
}
