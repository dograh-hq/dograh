"use client";

import 'react-international-phone/style.css';

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PhoneInput } from 'react-international-phone';

import {
    getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet,
    initiateCallApiV1TelephonyInitiateCallPost
} from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogClose,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { useUserConfig } from "@/context/UserConfigContext";

interface PhoneCallDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    getAccessToken: () => Promise<string>;
    user: { id: string; email?: string };
}

export const PhoneCallDialog = ({
    open,
    onOpenChange,
    workflowId,
    getAccessToken,
    user,
}: PhoneCallDialogProps) => {
    const router = useRouter();
    const { userConfig, saveUserConfig } = useUserConfig();
    const [phoneNumber, setPhoneNumber] = useState(userConfig?.test_phone_number || "");
    const [callLoading, setCallLoading] = useState(false);
    const [callError, setCallError] = useState<string | null>(null);
    const [callSuccessMsg, setCallSuccessMsg] = useState<string | null>(null);
    const [phoneChanged, setPhoneChanged] = useState(false);
    const [checkingConfig, setCheckingConfig] = useState(false);
    const [needsConfiguration, setNeedsConfiguration] = useState<boolean | null>(null);

    // Check telephony configuration when dialog opens
    useEffect(() => {
        const checkConfig = async () => {
            if (!open) return;

            setCheckingConfig(true);
            try {
                const accessToken = await getAccessToken();
                const configResponse = await getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet({
                    headers: { 'Authorization': `Bearer ${accessToken}` },
                });

                if (configResponse.error || (!configResponse.data?.twilio && !configResponse.data?.vonage && !configResponse.data?.vobiz)) {
                    setNeedsConfiguration(true);
                } else {
                    setNeedsConfiguration(false);
                }
            } catch (err) {
                console.error("Failed to check telephony config:", err);
                setNeedsConfiguration(false);
            } finally {
                setCheckingConfig(false);
            }
        };

        checkConfig();
    }, [open, getAccessToken]);

    // Reset state when dialog closes
    useEffect(() => {
        if (!open) {
            setCallError(null);
            setCallSuccessMsg(null);
            setCallLoading(false);
            setNeedsConfiguration(null);
        }
    }, [open]);

    // Keep phoneNumber in sync with userConfig when dialog opens
    useEffect(() => {
        if (open) {
            setPhoneNumber(userConfig?.test_phone_number || "");
            setPhoneChanged(false);
            setCallError(null);
            setCallSuccessMsg(null);
            setCallLoading(false);
        }
    }, [open, userConfig?.test_phone_number]);

    const handlePhoneInputChange = (formattedValue: string) => {
        setPhoneNumber(formattedValue);
        setPhoneChanged(formattedValue !== userConfig?.test_phone_number);
        setCallError(null);
        setCallSuccessMsg(null);
    };

    const handleConfigureContinue = () => {
        onOpenChange(false);
        router.push('/telephony-configurations');
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

            const response = await initiateCallApiV1TelephonyInitiateCallPost({
                body: {
                    workflow_id: workflowId,
                    phone_number: phoneNumber
                },
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
                const msg = response.data && (response.data as unknown as { message: string }).message || "Call initiated successfully!";
                setCallSuccessMsg(typeof msg === "string" ? msg : JSON.stringify(msg));
            }
        } catch (err: unknown) {
            setCallError(err instanceof Error ? err.message : "Failed to initiate call");
        } finally {
            setCallLoading(false);
        }
    };

    // Render loading state
    const renderLoading = () => (
        <>
            <DialogHeader>
                <DialogTitle>Phone Call</DialogTitle>
            </DialogHeader>
            <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
        </>
    );

    // Render configuration needed state
    const renderConfigurationNeeded = () => (
        <>
            <DialogHeader>
                <DialogTitle>Configure Telephony</DialogTitle>
                <DialogDescription>
                    You need to configure your telephony settings before making phone calls.
                    You will be redirected to the telephony configuration page.
                </DialogDescription>
            </DialogHeader>
            <DialogFooter>
                <Button variant="ghost" onClick={() => onOpenChange(false)}>
                    Do it Later
                </Button>
                <Button onClick={handleConfigureContinue}>
                    Continue
                </Button>
            </DialogFooter>
        </>
    );

    // Render phone call form
    const renderPhoneCallForm = () => (
        <>
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
                        onOpenChange(false);
                        router.push('/telephony-configurations');
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
                        <Button onClick={() => onOpenChange(false)}>
                            Close
                        </Button>
                    )}
                </div>
            </DialogFooter>
            {callError && <div className="text-red-500 text-sm mt-2">{callError}</div>}
            {callSuccessMsg && <div className="text-green-600 text-sm mt-2">{callSuccessMsg}</div>}
        </>
    );

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                {checkingConfig || needsConfiguration === null
                    ? renderLoading()
                    : needsConfiguration
                        ? renderConfigurationNeeded()
                        : renderPhoneCallForm()
                }
            </DialogContent>
        </Dialog>
    );
};
