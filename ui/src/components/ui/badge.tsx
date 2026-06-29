import { cva, type VariantProps } from "class-variance-authority"
import * as React from "react"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
    "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
    {
        variants: {
            variant: {
                default:
                    "border-transparent bg-primary text-primary-foreground hover:bg-primary/85",
                secondary:
                    "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
                destructive:
                    "border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/85",
                outline: "border-border/70 text-foreground",
                success:
                    "border-transparent bg-success text-primary-foreground hover:bg-success/85",
                warning:
                    "border-transparent bg-warning text-cta-foreground hover:bg-warning/85",
                muted:
                    "border-transparent bg-muted text-muted-foreground hover:bg-muted/80",
            },
        },
        defaultVariants: {
            variant: "default",
        },
    }
)

export interface BadgeProps
    extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> { }

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
    ({ className, variant, ...props }, ref) => {
        return (
            <div
                ref={ref}
                className={cn(badgeVariants({ variant }), className)}
                {...props}
            />
        )
    }
)
Badge.displayName = "Badge"

export { Badge, badgeVariants }
