import { AlertCircle, CreditCard, Key } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

interface ApiKeyErrorDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    error: string | null;
    errorCode: string | null;
    onNavigateToBilling: () => void;
    onNavigateToModelConfig: () => void;
}

export const ApiKeyErrorDialog = ({
    open,
    onOpenChange,
    error,
    errorCode,
    onNavigateToBilling,
    onNavigateToModelConfig,
}: ApiKeyErrorDialogProps) => {
    const isBillingCreditsError = errorCode === 'insufficient_credits';
    const isQuotaError = isBillingCreditsError || errorCode === 'quota_exceeded';

    const title = isQuotaError ? "Insufficient Credits" : "API Configuration Error";
    const icon = isQuotaError ? <CreditCard className="h-5 w-5 text-orange-500" /> : <Key className="h-5 w-5 text-red-500" />;
    const buttonText = isBillingCreditsError ? "Go to Billing" : "Go to Model Configurations";
    const onNavigate = isBillingCreditsError ? onNavigateToBilling : onNavigateToModelConfig;

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
                                {isBillingCreditsError && (
                                    <p className="text-muted-foreground">
                                        Purchase credits from Billing to continue using Dograh-managed models.
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
                    <Button onClick={onNavigate}>
                        {buttonText}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
};
