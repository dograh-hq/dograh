import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import { useTranslations } from 'next-intl';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

interface ModelConfigurationDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowConfigurations: WorkflowConfigurations | null;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}

export const ModelConfigurationDialog = ({
    open,
    onOpenChange,
    workflowConfigurations,
    workflowName,
    onSave,
}: ModelConfigurationDialogProps) => {
    const t = useTranslations("workflowList");
    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{t("modelConfiguration")}</DialogTitle>
                    <DialogDescription>
                        {t("modelConfigurationDesc")}
                    </DialogDescription>
                </DialogHeader>

                <ServiceConfigurationForm
                    mode="override"
                    currentOverrides={workflowConfigurations?.model_overrides}
                    submitLabel="Save"
                    onSave={async (config) => {
                        await onSave(
                            {
                                ...workflowConfigurations,
                                model_overrides: config.model_overrides as WorkflowConfigurations["model_overrides"],
                            } as WorkflowConfigurations,
                            workflowName,
                        );
                        onOpenChange(false);
                    }}
                />
            </DialogContent>
        </Dialog>
    );
};
