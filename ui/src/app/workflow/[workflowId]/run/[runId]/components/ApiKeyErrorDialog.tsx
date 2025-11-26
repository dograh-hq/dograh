import { AlertCircle, CreditCard, Key } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface ApiKeyErrorDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    error: string | null;
    onNavigateToApiKeys: () => void;
}

export const ApiKeyErrorDialog = ({
    open,
    onOpenChange,
    error,
    onNavigateToApiKeys
}: ApiKeyErrorDialogProps) => {
    // Check if this is a quota error based on the error message
    const isQuotaError = error?.toLowerCase().includes('insufficient') ||
                        error?.toLowerCase().includes('credits') ||
                        error?.toLowerCase().includes('quota');

    const title = isQuotaError ? "Insufficient Credits" : "API Configuration Error";
    const icon = isQuotaError ? <CreditCard className="h-5 w-5 text-orange-500" /> : <Key className="h-5 w-5 text-red-500" />;
    const buttonText = isQuotaError ? "Add Credits" : "Go to API Keys Settings";

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        {icon}
                        {title}
                    </DialogTitle>
                    <DialogDescription className="pt-3" asChild>
                        <div className="flex items-start gap-2">
                            <AlertCircle className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                            <div className="text-sm space-y-1">
                                <p className="font-medium text-foreground">{error}</p>
                                {isQuotaError && (
                                    <p className="text-muted-foreground">
                                        Your Dograh service credits are too low to start a call.
                                    </p>
                                )}
                            </div>
                        </div>
                    </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={onNavigateToApiKeys}>
                        {buttonText}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
