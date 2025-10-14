import 'react-international-phone/style.css';

import { ReactFlowInstance, ReactFlowJsonObject } from "@xyflow/react";
import { AlertTriangle, CheckCheck, Download, LoaderCircle, Phone, ShieldCheck } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { PhoneInput } from 'react-international-phone';

import { getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet, initiateCallApiV1TelephonyInitiateCallPost } from '@/client/sdk.gen';
import { WorkflowError } from '@/client/types.gen';
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { OnboardingTooltip } from '@/components/onboarding/OnboardingTooltip';
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { WORKFLOW_RUN_MODES } from '@/constants/workflowRunModes';
import { useOnboarding } from '@/context/OnboardingContext';
import { useUserConfig } from "@/context/UserConfigContext";
import { useAuth } from '@/lib/auth';

interface WorkflowHeaderProps {
    isDirty: boolean;
    workflowName: string;
    rfInstance: React.RefObject<ReactFlowInstance<FlowNode, FlowEdge> | null>;
    onRun: (mode: string) => Promise<void>;
    workflowId: number;
    workflowValidationErrors: WorkflowError[];
    saveWorkflow: (updateWorkflowDefinition?: boolean) => Promise<void>;
}

const handleExport = (workflow_name: string, workflow_definition: ReactFlowJsonObject<FlowNode, FlowEdge> | undefined) => {
    if (!workflow_definition) return { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } };

    const exportData = {
        name: workflow_name,
        workflow_definition: workflow_definition
    };

    // Convert to JSON string with proper formatting
    const jsonString = JSON.stringify(exportData, null, 2);

    // Create a blob with the JSON data
    const blob = new Blob([jsonString], { type: 'application/json' });

    // Create a download link
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${workflow_name.replace(/\s+/g, '_')}.json`;

    // Trigger download
    document.body.appendChild(link);
    link.click();

    // Cleanup
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
};

const WorkflowHeader = ({ isDirty, workflowName, rfInstance, onRun, workflowId, workflowValidationErrors, saveWorkflow }: WorkflowHeaderProps) => {
    const router = useRouter();
    const { userConfig, saveUserConfig } = useUserConfig();
    const { hasSeenTooltip, markTooltipSeen } = useOnboarding();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [phoneNumber, setPhoneNumber] = useState(userConfig?.test_phone_number || "");
    const [savingWorkflow, setSavingWorkflow] = useState(false);
    const [callLoading, setCallLoading] = useState(false);
    const [callError, setCallError] = useState<string | null>(null);
    const [callSuccessMsg, setCallSuccessMsg] = useState<string | null>(null);
    const [phoneChanged, setPhoneChanged] = useState(false);
    const [validationDialogOpen, setValidationDialogOpen] = useState(false);
    const [configureDialogOpen, setConfigureDialogOpen] = useState(false);
    const { user, getAccessToken } = useAuth();
    const webCallButtonRef = useRef<HTMLButtonElement>(null);

    const hasValidationErrors = workflowValidationErrors.length > 0;

    // Reset call-related state whenever the dialog is closed so that a new call can be placed
    useEffect(() => {
        if (!dialogOpen) {
            setCallError(null);
            setCallSuccessMsg(null);
            setCallLoading(false);
        }
    }, [dialogOpen]);

    // Keep phoneNumber in sync with userConfig when dialog opens
    const handleDialogOpenChange = (open: boolean) => {
        setDialogOpen(open);
        if (open) {
            setPhoneNumber(userConfig?.test_phone_number || "");
            setPhoneChanged(false);
            setCallError(null);
            setCallSuccessMsg(null);
            setCallLoading(false);
        }
    };

    const handlePhoneInputChange = (
        formattedValue: string
    ) => {
        // `value` is the raw E.164 value, e.g. "+14155552671"
        setPhoneNumber(formattedValue);
        setPhoneChanged(formattedValue !== userConfig?.test_phone_number);

        // clear any prior errors, etc.
        setCallError(null);
        setCallSuccessMsg(null);
    };

    const handlePhoneCallClick = async () => {
        // Check telephony configuration before opening dialog
        try {
            const accessToken = await getAccessToken();
            const configResponse = await getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet({
                headers: { 'Authorization': `Bearer ${accessToken}` },
            });

            // If no configuration exists, show configure dialog
            // Check if Twilio is configured (currently the only supported provider)
            if (configResponse.error || !configResponse.data?.twilio) {
                setConfigureDialogOpen(true);
                return;
            }

            // Configuration exists, open the phone call dialog
            setDialogOpen(true);
        } catch (err: unknown) {
            console.error("Failed to check telephony config:", err);
            // Still open dialog to show the error
            setDialogOpen(true);
        }
    };

    const handleConfigureContinue = () => {
        setConfigureDialogOpen(false);
        router.push(`/configure-telephony?returnTo=/workflow/${workflowId}`);
    };

    const handleStartCall = async () => {
        setCallLoading(true);
        setCallError(null);
        setCallSuccessMsg(null);
        try {
            if (!user || !userConfig) return;
            const accessToken = await getAccessToken();

            // Save phone number if it has changed
            if (phoneChanged) {
                await saveUserConfig({ ...userConfig, test_phone_number: phoneNumber });
                setPhoneChanged(false);
            }

            // Configuration exists, proceed with call initiation
            const response = await initiateCallApiV1TelephonyInitiateCallPost({
                body: { workflow_id: workflowId },
                headers: { 'Authorization': `Bearer ${accessToken}` },
            });

            if (response.error) {
                let errMsg = "Failed to initiate call";
                if (typeof response.error === "string") {
                    errMsg = response.error;
                } else if (response.error && typeof response.error === "object") {
                    errMsg = (response.error as unknown as { detail: string }).detail || JSON.stringify(response.error);
                }
                setCallError(errMsg);
            } else {
                // Try to show a message from the response, fallback to generic
                const msg = response.data && (response.data as unknown as { message: string }).message || "Call initiated successfully!";
                setCallSuccessMsg(typeof msg === "string" ? msg : JSON.stringify(msg));
            }
        } catch (err: unknown) {
            setCallError(err instanceof Error ? err.message : "Failed to initiate call");
        } finally {
            setCallLoading(false);
        }
    };

    return (
        <div className="flex items-center gap-2">
            <TooltipProvider>
                <Tooltip>
                    <TooltipTrigger asChild>
                        <div className="flex items-center gap-1 text-sm text-gray-500 mr-2">
                            {hasValidationErrors ? (
                                <AlertTriangle className="h-4 w-4 text-red-500" />
                            ) : (
                                <ShieldCheck className="h-4 w-4 text-green-500" />
                            )}
                            <span>{hasValidationErrors ? 'Invalid' : 'Valid'}</span>
                            {hasValidationErrors && (
                                <Button
                                    size="sm"
                                    className="ml-1 h-6 px-2 text-xs"
                                    onClick={() => setValidationDialogOpen(true)}
                                >
                                    View Issues
                                </Button>
                            )}
                        </div>
                    </TooltipTrigger>
                    <TooltipContent>
                        {hasValidationErrors
                            ? `Workflow has ${workflowValidationErrors.length} validation ${workflowValidationErrors.length === 1 ? 'issue' : 'issues'}`
                            : 'Workflow is valid'}
                    </TooltipContent>
                </Tooltip>
            </TooltipProvider>

            <Button
                variant="outline"
                size="sm"
                onClick={() => handleExport(workflowName, rfInstance.current?.toObject())}
            >
                <Download className="mr-2 h-4 w-4" />
                Export Pathway
            </Button>
            <Button
                ref={webCallButtonRef}
                variant="outline"
                size="sm"
                onClick={() => {
                    // Mark the tooltip as seen when the button is clicked
                    if (!hasSeenTooltip('web_call')) {
                        markTooltipSeen('web_call');
                    }
                    onRun(WORKFLOW_RUN_MODES.SMALL_WEBRTC);
                }}
                disabled={hasValidationErrors}
            >
                <Phone className="mr-2 h-4 w-4" />
                Web Call
            </Button>
            <Button
                variant="outline"
                size="sm"
                onClick={handlePhoneCallClick}
                disabled={hasValidationErrors}
            >
                <Phone className="mr-2 h-4 w-4" />
                Phone Call
            </Button>

            {isDirty ? (
                <Button
                    variant="default"
                    size="sm"
                    onClick={async () => {
                        setSavingWorkflow(true);
                        await saveWorkflow();
                        setSavingWorkflow(false);
                    }}
                    disabled={savingWorkflow}
                    className="animate-pulse"
                >
                    {savingWorkflow ? (
                        <>
                            <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
                            Saving...
                        </>
                    ) : (
                        'Save Changes'
                    )}
                </Button>
            ) : (
                <div className="flex items-center gap-1 text-sm text-gray-500">
                    <CheckCheck className="h-4 w-4 text-green-500" />
                    <span className='mr-2'>Saved</span>
                </div>
            )}

            {/* Validation Errors Dialog */}
            <Dialog open={validationDialogOpen} onOpenChange={setValidationDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Workflow Validation Issues</DialogTitle>
                        <DialogDescription>
                            Please fix the following issues before running the workflow.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="max-h-[60vh] overflow-y-auto">
                        <ul className="space-y-2">
                            {workflowValidationErrors.map((error, index) => (
                                <li key={index} className="border-l-2 border-red-500 pl-3 py-2">
                                    <div className="font-medium">{error.message}</div>
                                    {error.id && (
                                        <div className="text-sm text-gray-500">
                                            {error.kind === 'node' ? 'Node' : error.kind === 'edge' ? 'Edge' : 'Workflow'} ID: {error.id}
                                        </div>
                                    )}
                                    {error.field && (
                                        <div className="text-sm mt-1">
                                            Field: {error.field}
                                        </div>
                                    )}
                                </li>
                            ))}
                        </ul>
                    </div>
                    <DialogFooter>
                        <Button onClick={() => setValidationDialogOpen(false)}>
                            Close
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Phone Call Dialog */}
            <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Phone Call</DialogTitle>
                        <DialogDescription>
                            Enter the phone number to call. The number will be saved automatically.
                        </DialogDescription>
                    </DialogHeader>
                    <PhoneInput
                        defaultCountry="in"
                        value={phoneNumber}
                        onChange={handlePhoneInputChange}
                    />
                    <DialogFooter className="flex-col sm:flex-row gap-2">
                        <Button
                            variant="outline"
                            onClick={() => {
                                setDialogOpen(false);
                                router.push(`/configure-telephony?returnTo=/workflow/${workflowId}`);
                            }}
                        >
                            Configure Telephony
                        </Button>
                        <div className="flex gap-2 flex-1 justify-end">
                            <DialogClose asChild>
                                <Button variant="outline">Cancel</Button>
                            </DialogClose>
                            {!callSuccessMsg ? (
                                <Button
                                    onClick={handleStartCall}
                                    disabled={callLoading || !phoneNumber}
                                >
                                    {callLoading ? "Calling..." : "Start Call"}
                                </Button>
                            ) : (
                                <Button onClick={() => setDialogOpen(false)}>
                                    Close
                                </Button>
                            )}
                        </div>
                    </DialogFooter>
                    {callError && <div className="text-red-500 text-sm mt-2">{callError}</div>}
                    {callSuccessMsg && <div className="text-green-600 text-sm mt-2">{callSuccessMsg}</div>}
                </DialogContent>
            </Dialog>

            {/* Configure Telephony Dialog */}
            <Dialog open={configureDialogOpen} onOpenChange={setConfigureDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Configure Telephony</DialogTitle>
                        <DialogDescription>
                            You need to configure your telephony settings before making phone calls.
                            You will be redirected to the telephony configuration page.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="ghost" onClick={() => setConfigureDialogOpen(false)}>
                            Do it Later
                        </Button>
                        <Button onClick={handleConfigureContinue}>
                            Continue
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Onboarding Tooltip */}
            <OnboardingTooltip
                title='Test your Voice Agent'
                targetRef={webCallButtonRef}
                message="Test this workflow now in your browser using Web Call"
                onDismiss={() => markTooltipSeen('web_call')}
                showNext={false}
                isVisible={!hasSeenTooltip('web_call') && !hasValidationErrors}
            />
        </div>
    );
};

export default WorkflowHeader;
