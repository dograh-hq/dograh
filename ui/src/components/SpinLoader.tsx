import { Loader2 } from "lucide-react";

interface SpinLoaderProps {
    label?: string;
}

export default function SpinLoader({ label }: SpinLoaderProps) {
    return (
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
            <Loader2 className="h-8 w-8 animate-spin text-foreground" />
            {label && <span>{label}</span>}
        </div>
    );
}
