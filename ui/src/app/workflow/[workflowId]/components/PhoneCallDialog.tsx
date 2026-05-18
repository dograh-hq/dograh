"use client";

import 'react-international-phone/style.css';

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { PhoneInput } from 'react-international-phone';

import {
    initiateCallApiV1TelephonyInitiateCallPost,
    listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet
} from '@/client/sdk.gen';
import type { PhoneNumberResponse, TelephonyConfigurationListItem } from '@/client/types.gen';
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useUserConfig } from "@/context/UserConfigContext";

interface PhoneCallDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    user: { id: string; email?: string };
}

export const PhoneCallDialog = ({
    open,
    onOpenChange,
    workflowId,
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
    const [sipMode, setSipMode] = useState(() => /^(PJSIP|SIP)\//i.test(userConfig?.test_phone_number || ""));
    const [telephonyConfigs, setTelephonyConfigs] = useState<TelephonyConfigurationListItem[]>([]);
    const [selectedConfigId, setSelectedConfigId] = useState<string>("");
    const [fromPhoneNumbers, setFromPhoneNumbers] = useState<PhoneNumberResponse[]>([]);
    const [selectedFromPhoneNumberId, setSelectedFromPhoneNumberId] = useState<string>("");
    const [loadingPhoneNumbers, setLoadingPhoneNumbers] = useState(false);

    // Check telephony configuration when dialog opens
    useEffect(() => {
        const checkConfig = async () => {
            if (!open) return;

            setCheckingConfig(true);
            try {
                const configResponse = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({});

                const configurations = configResponse.data?.configurations ?? [];
                if (configResponse.error || configurations.length === 0) {
                    setNeedsConfiguration(true);
                    setTelephonyConfigs([]);
                    setSelectedConfigId("");
                } else {
                    setNeedsConfiguration(false);
                    setTelephonyConfigs(configurations);
                    const defaultConfig =
                        configurations.find((c) => c.is_default_outbound) ?? configurations[0];
                    setSelectedConfigId(String(defaultConfig.id));
                }
            } catch (err) {
                console.error("Failed to check telephony config:", err);
                setNeedsConfiguration(false);
                setTelephonyConfigs([]);
                setSelectedConfigId("");
            } finally {
                setCheckingConfig(false);
            }
        };

        checkConfig();
    }, [open]);

    // Reset state when dialog closes
    useEffect(() => {
        if (!open) {
            setCallError(null);
            setCallSuccessMsg(null);
            setCallLoading(false);
            setNeedsConfiguration(null);
            setTelephonyConfigs([]);
            setSelectedConfigId("");
            setFromPhoneNumbers([]);
            setSelectedFromPhoneNumberId("");
        }
    }, [open]);

    // Fetch phone numbers whenever the selected telephony configuration changes.
    useEffect(() => {
        if (!open || !selectedConfigId) {
            setFromPhoneNumbers([]);
            setSelectedFromPhoneNumberId("");
            return;
        }

        let cancelled = false;
        const fetchPhoneNumbers = async () => {
            setLoadingPhoneNumbers(true);
            try {
                const response = await listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet({
                    path: { config_id: Number(selectedConfigId) },
                });
                if (cancelled) return;

                const all = response.data?.phone_numbers ?? [];
                const active = all.filter((p) => p.is_active);
                setFromPhoneNumbers(active);
                const defaultPhone = active.find((p) => p.is_default_caller_id) ?? active[0];
                setSelectedFromPhoneNumberId(defaultPhone ? String(defaultPhone.id) : "");
            } catch (err) {
                if (cancelled) return;
                console.error("Failed to load phone numbers for config:", err);
                setFromPhoneNumbers([]);
                setSelectedFromPhoneNumberId("");
            } finally {
                if (!cancelled) setLoadingPhoneNumbers(false);
            }
        };

        fetchPhoneNumbers();
        return () => {
            cancelled = true;
        };
    }, [open, selectedConfigId]);

    // Keep phoneNumber in sync with userConfig when dialog opens
    useEffect(() => {
        if (open) {
            const saved = userConfig?.test_phone_number || "";
            setPhoneNumber(saved);
            setSipMode(/^(PJSIP|SIP)\//i.test(saved));
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

            // Save phone number if it has changed
            if (phoneChanged) {
                await saveUserConfig({ ...userConfig, test_phone_number: phoneNumber });
                setPhoneChanged(false);
            }

            const response = await initiateCallApiV1TelephonyInitiateCallPost({
                body: {
                    workflow_id: workflowId,
                    phone_number: phoneNumber,
                    telephony_configuration_id: selectedConfigId ? Number(selectedConfigId) : null,
                    from_phone_number_id: selectedFromPhoneNumberId ? Number(selectedFromPhoneNumberId) : null,
                },
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
                    Enter the phone number or SIP endpoint to call. The number will be saved automatically.
                </DialogDescription>
            </DialogHeader>
            {telephonyConfigs.length > 0 && (
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="telephony-config">Telephony configuration</Label>
                    <Select value={selectedConfigId} onValueChange={setSelectedConfigId}>
                        <SelectTrigger id="telephony-config" className="w-full">
                            <SelectValue placeholder="Select a configuration" />
                        </SelectTrigger>
                        <SelectContent>
                            {telephonyConfigs.map((config) => (
                                <SelectItem key={config.id} value={String(config.id)}>
                                    {config.name} ({config.provider})
                                    {config.is_default_outbound ? " — default" : ""}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            )}
            {selectedConfigId && (
                <div className="flex flex-col gap-1.5">
                    <Label htmlFor="from-phone-number">Caller ID (from)</Label>
                    {loadingPhoneNumbers ? (
                        <div className="flex items-center text-sm text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin mr-2" />
                            Loading phone numbers...
                        </div>
                    ) : fromPhoneNumbers.length > 0 ? (
                        <Select
                            value={selectedFromPhoneNumberId}
                            onValueChange={setSelectedFromPhoneNumberId}
                        >
                            <SelectTrigger id="from-phone-number" className="w-full">
                                <SelectValue placeholder="Select a phone number" />
                            </SelectTrigger>
                            <SelectContent>
                                {fromPhoneNumbers.map((phone) => (
                                    <SelectItem key={phone.id} value={String(phone.id)}>
                                        {phone.label ? `${phone.label} — ${phone.address}` : phone.address}
                                        {phone.is_default_caller_id ? " — default" : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    ) : (
                        <div className="text-xs text-muted-foreground">
                            No phone numbers in this configuration. The provider will pick one automatically.
                        </div>
                    )}
                </div>
            )}
            {sipMode ? (
                <Input
                    value={phoneNumber}
                    onChange={(e) => handlePhoneInputChange(e.target.value)}
                    placeholder="PJSIP/1234 or SIP/1234"
                />
            ) : (
                <PhoneInput
                    defaultCountry="in"
                    value={phoneNumber}
                    onChange={handlePhoneInputChange}
                />
            )}
            <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground underline"
                onClick={() => { setSipMode(!sipMode); setPhoneNumber(""); setPhoneChanged(true); }}
            >
                {sipMode ? "Use phone number instead" : "Use SIP endpoint instead"}
            </button>
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
                        <>
                            <Button variant="outline" onClick={() => { setCallSuccessMsg(null); setCallError(null); }}>
                                Call Again
                            </Button>
                            <Button onClick={() => onOpenChange(false)}>
                                Close
                            </Button>
                        </>
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
