import { Position } from "@xyflow/react";
import { ReactNode } from "react";

import { BaseHandle } from "@/components/flow/nodes/BaseHandle";
import { BaseNode } from "@/components/flow/nodes/BaseNode";
import { NodeHeader, NodeHeaderIcon, NodeHeaderTitle } from "@/components/flow/nodes/NodeHeader";

interface NodeContentProps {
    selected: boolean;
    invalid?: boolean;
    selected_through_edge?: boolean;
    hovered_through_edge?: boolean;
    title: string;
    icon: ReactNode;
    bgColor: string;
    hasSourceHandle?: boolean;
    hasTargetHandle?: boolean;
    children?: ReactNode;
    className?: string;
    onDoubleClick?: () => void;
    nodeId?: string;
}

export const NodeContent = ({
    selected,
    invalid,
    selected_through_edge,
    hovered_through_edge,
    title,
    icon,
    bgColor,
    hasSourceHandle = false,
    hasTargetHandle = false,
    children,
    className = "",
    onDoubleClick,
    nodeId,
}: NodeContentProps) => {
    return (
        <BaseNode
            selected={selected}
            invalid={invalid}
            selected_through_edge={selected_through_edge}
            hovered_through_edge={hovered_through_edge}
            className={`p-0 overflow-hidden ${className}`}
            onDoubleClick={onDoubleClick}
        >
            {hasTargetHandle && <BaseHandle type="target" position={Position.Top} />}
            <NodeHeader className={`px-3 py-2 border-b ${bgColor}`}>
                <NodeHeaderIcon>{icon}</NodeHeaderIcon>
                <NodeHeaderTitle>{title} - NodeID: {nodeId}</NodeHeaderTitle>
            </NodeHeader>
            <div className="p-3">
                {children}
            </div>
            {hasSourceHandle && <BaseHandle type="source" position={Position.Bottom} />}
        </BaseNode>
    );
};
