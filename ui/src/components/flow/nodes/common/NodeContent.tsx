import { Position } from "@xyflow/react";
import { ReactNode } from "react";

import { BaseHandle } from "@/components/flow/nodes/BaseHandle";
import { BaseNode } from "@/components/flow/nodes/BaseNode";
import { cn } from "@/lib/utils";

interface NodeContentProps {
    selected: boolean;
    invalid?: boolean;
    selected_through_edge?: boolean;
    hovered_through_edge?: boolean;
    runtimeActive?: boolean;
    title: string;
    icon: ReactNode;
    badgeLabel?: string;
    badgeClassName?: string;
    contentLabel?: string;
    hasSourceHandle?: boolean;
    hasTargetHandle?: boolean;
    children?: ReactNode;
    className?: string;
    onDoubleClick?: () => void;
    nodeId?: string;
}

// Get badge styling based on node type
const DEFAULT_BADGE = { label: 'Node', className: 'bg-zinc-500 text-white' };

export const NodeContent = ({
    selected,
    invalid,
    selected_through_edge,
    hovered_through_edge,
    runtimeActive,
    title,
    icon,
    badgeLabel,
    badgeClassName,
    contentLabel = "Prompt",
    hasSourceHandle = false,
    hasTargetHandle = false,
    children,
    className = "",
    onDoubleClick,
    nodeId,
}: NodeContentProps) => {
    const badge = {
        label: badgeLabel ?? DEFAULT_BADGE.label,
        className: badgeClassName ?? DEFAULT_BADGE.className,
    };

    return (
        <BaseNode
            selected={selected}
            invalid={invalid}
            selected_through_edge={selected_through_edge}
            hovered_through_edge={hovered_through_edge}
            runtimeActive={runtimeActive}
            className={`p-0 ${className}`}
            onDoubleClick={onDoubleClick}
        >
            {hasTargetHandle && <BaseHandle type="target" position={Position.Top} />}

            {/* Node type badge - positioned at top */}
            <div className="absolute -top-3 left-4">
                <span className={cn(
                    "inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium",
                    badge.className
                )}>
                    <span className="[&>*]:w-3 [&>*]:h-3">{icon}</span>
                    {badge.label}
                </span>
            </div>

            {/* Header with title */}
            <div className="px-4 pt-5 pb-2 border-b border-border">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-foreground truncate">
                        {title}
                        {nodeId && (
                            <span className="ml-2 text-xs font-normal text-muted-foreground">
                                #{nodeId}
                            </span>
                        )}
                    </h3>
                </div>
            </div>

            {/* Content area with prompt label */}
            <div className="p-4">
                <div className="text-xs text-muted-foreground mb-1.5 font-medium">
                    {contentLabel}:
                </div>
                {children}
            </div>

            {hasSourceHandle && <BaseHandle type="source" position={Position.Bottom} />}
        </BaseNode>
    );
};
