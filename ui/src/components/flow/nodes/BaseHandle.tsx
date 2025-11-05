import { Handle, HandleProps } from "@xyflow/react";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

export type BaseHandleProps = HandleProps;

export const BaseHandle = forwardRef<HTMLDivElement, BaseHandleProps>(
    ({ className, children, type, ...props }, ref) => {
        const isSource = type === 'source';
        const isTarget = type === 'target';

        return (
            <Handle
                ref={ref}
                type={type}
                {...props}
                className={cn(
                    "transition-all hover:!bg-blue-500",
                    // Source (outgoing) has larger visible handle for easier connection
                    isSource && "!h-[16px] !w-[16px] rounded-full",
                    // Target (incoming) smaller rectangle
                    isTarget && "!h-[10px] !w-[14px] rounded-sm",
                    className,
                )}
                style={{
                    border: 'none',
                    background: '#94A3B8', // slate-400
                    ...props.style,
                }}
            >
                {children}
            </Handle>
        );
    },
);

BaseHandle.displayName = "BaseHandle";
