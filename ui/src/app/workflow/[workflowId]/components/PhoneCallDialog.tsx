"use client";

import 'react-international-phone/style.css';

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { PhoneInput } from 'react-international-phone';

import {
    getPreferencesApiV1OrganizationsPreferencesGet,
    getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet,
    initiateCallApiV1TelephonyInitiateCallPost,
    listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
    savePreferencesApiV1OrganizationsPreferencesPut,
} from '@/client/sdk.gen';
import type {
    OrganizationPreferences,
    PhoneNumberResponse,
    TelephonyConfigurationListItem,
} from '@/client/types.gen';
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
import { detailFromError } from "@/lib/apiError";

interface PhoneCallDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    user: { id: string; email?: string };
}

/** A configuration the backend will accept an outbound call on right now. */
const isCallable = (config: TelephonyConfigurationListItem) =>
    config.is_ready_for_outbound !== false && !config.inactive;

/** "Twilio, Plivo and Telnyx" — names come from the registry, never hardcoded. */
const joinNames = (names: string[]) => {
    if (names.length === 0) return "a telephony provider";
    if (names.length === 1) return names[0];
    return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
};

export const PhoneCallDialog = ({
    open,
    onOpenChange,
    workflowId,
    user,
}: PhoneCallDialogProps) => {
    const router = useRouter();
    const { refreshConfig } = useUserConfig();
    const [preferences, setPreferences] = useState<OrganizationPreferences>({});
    const [preferencesLoaded, setPreferencesLoaded] = useState(false);
    const [phoneNumber, setPhoneNumber] = useState("");
    const [callLoading, setCallLoading] = useState(false);
    const [callError, setCallError] = useState<string | null>(null);
    const [callSuccessMsg, setCallSuccessMsg] = useState<string | null>(null);
    const [phoneChanged, setPhoneChanged] = useState(false);
    const [checkingConfig, setCheckingConfig] = useState(false);
    const [needsConfiguration, setNeedsConfiguration] = useState<boolean | null>(null);
    const [sipMode, setSipMode] = useState(false);
    const [telephonyConfigs, setTelephonyConfigs] = useState<TelephonyConfigurationListItem[]>([]);
    const [selectedConfigId, setSelectedConfigId] = useState<string>("");
    const [fromPhoneNumbers, setFromPhoneNumbers] = useState<PhoneNumberResponse[]>([]);
    const [selectedFromPhoneNumberId, setSelectedFromPhoneNumberId] = useState<string>("");
    const [loadingPhoneNumbers, setLoadingPhoneNumbers] = useState(false);
    const [apiProviderNames, setApiProviderNames] = useState<string[]>([]);

    const fetchPreferences = useCallback(async () => {
        const result =
            await getPreferencesApiV1OrganizationsPreferencesGet();
        if (result.error) {
            throw new Error(detailFromError(result.error, "Failed to load phone preferences"));
        }
        return result.data || {};
    }, []);

    const applyPreferences = useCallback((nextPreferences: OrganizationPreferences) => {
        const saved = nextPreferences.test_phone_number || "";
        setPreferences(nextPreferences);
        setPhoneNumber(saved);
        setSipMode(/^(PJSIP|SIP)\//i.test(saved));
        setPhoneChanged(false);
    }, []);

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
                    // Prefer a configuration that can actually dial. Every org
                    // is provisioned with a managed SIP configuration that has
                    // no carrier and no caller ID until the user sets one up,
                    // so "a configuration exists" is not "a call can be placed".
                    const callable = configurations.filter(isCallable);
                    const defaultConfig =
                        callable.find((c) => c.is_default_outbound) ??
                        callable[0] ??
                        configurations.find((c) => c.is_default_outbound) ??
                        configurations[0];
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

    // Load organization-scoped call preferences when dialog opens.
    useEffect(() => {
        if (!open) return;

        let cancelled = false;
        setPreferencesLoaded(false);

        const loadPreferences = async () => {
            try {
                const nextPreferences = await fetchPreferences();
                if (cancelled) return;
                applyPreferences(nextPreferences);
                setPreferencesLoaded(true);
            } catch (err) {
                if (cancelled) return;
                applyPreferences({});
                setPreferencesLoaded(false);
                setCallError(err instanceof Error ? err.message : "Failed to load phone preferences");
            }
        };

        loadPreferences();
        return () => {
            cancelled = true;
        };
    }, [applyPreferences, fetchPreferences, open]);

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

    const handlePhoneInputChange = (formattedValue: string) => {
        setPhoneNumber(formattedValue);
        setPhoneChanged(formattedValue !== (preferences.test_phone_number || ""));
        setCallError(null);
        setCallSuccessMsg(null);
    };

    const selectedConfig = telephonyConfigs.find(
        (config) => String(config.id) === selectedConfigId,
    );
    const selectedConfigBlocked =
        selectedConfig !== undefined && !isCallable(selectedConfig);
    // Nothing here can place a call: either the org has no configurations, or
    // the ones it has are still waiting on the customer's own carrier. An org
    // whose only configurations are *inactive* is a different problem, so it
    // falls through to the form rather than getting setup instructions.
    const needsPhoneService =
        needsConfiguration === true ||
        (!telephonyConfigs.some(isCallable) &&
            telephonyConfigs.some(
                (config) => !config.inactive && config.is_ready_for_outbound === false,
            ));

    const goToConfiguration = (target?: { configId?: number; add?: boolean }) => {
        onOpenChange(false);
        if (target?.configId) {
            router.push(`/telephony-configurations/${target.configId}`);
            return;
        }
        router.push(
            target?.add
                ? '/telephony-configurations?add=1'
                : '/telephony-configurations',
        );
    };

    const savePhoneNumberPreference = async () => {
        const currentPreferences = preferencesLoaded ? preferences : await fetchPreferences();
        const result =
            await savePreferencesApiV1OrganizationsPreferencesPut({
                body: {
                    ...currentPreferences,
                    test_phone_number: phoneNumber || null,
                },
            });

        if (result.error) {
            throw new Error(detailFromError(result.error, "Failed to save phone preferences"));
        }
        if (!result.data) {
            throw new Error("Failed to save phone preferences");
        }

        setPreferences(result.data);
        setPreferencesLoaded(true);
        setPhoneChanged(false);
        await refreshConfig();
    };

    const handleStartCall = async () => {
        setCallLoading(true);
        setCallError(null);
        setCallSuccessMsg(null);
        try {
            if (!user) return;

            // Save phone number if it has changed
            if (phoneChanged) {
                await savePhoneNumberPreference();
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

    // Provider names for the "connect phone service" copy. Fetched only when
    // that screen is actually reached — an org that can already dial never
    // pays for it.
    useEffect(() => {
        if (!open || !needsPhoneService) return;

        let cancelled = false;
        (async () => {
            const response =
                await getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet({});
            if (cancelled) return;
            setApiProviderNames(
                (response.data?.providers ?? [])
                    .filter((provider) => provider.connectivity !== "sip")
                    .map((provider) => provider.display_name),
            );
        })();
        return () => {
            cancelled = true;
        };
    }, [open, needsPhoneService]);

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

    // Render the "no way to place a call yet" state.
    //
    // Two genuinely different routes to phone service, so offer both rather
    // than pushing everyone down the SIP path: most people just want a carrier
    // account, and only those who already run a trunk or PBX want SIP. Neither
    // option names a provider — both are derived from the registry, because
    // Cloudonix won't stay the only SIP connector.
    const renderConnectPhoneService = () => {
        // A SIP connection the org already has (every hosted signup is
        // provisioned one) is worth deep-linking to; otherwise they add one.
        const sipConfig = telephonyConfigs.find(
            (config) => config.connectivity === "sip" && !config.inactive,
        );
        const blockedReason = telephonyConfigs.find(
            (config) => !config.inactive && config.outbound_blocked_reason,
        )?.outbound_blocked_reason;

        return (
            <>
                <DialogHeader>
                    <DialogTitle>Connect phone service</DialogTitle>
                    <DialogDescription>
                        Dograh doesn&apos;t sell phone numbers or minutes. Choose how
                        this agent should place and receive calls.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex flex-col gap-3">
                    <div className="rounded-lg border p-4 space-y-3">
                        <div className="space-y-1">
                            <div className="flex items-center gap-2">
                                <h3 className="text-sm font-medium">
                                    Use a telephony provider
                                </h3>
                                <span className="rounded-full bg-teal-600/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-teal-700 dark:text-teal-400">
                                    Recommended
                                </span>
                            </div>
                            <p className="text-sm text-muted-foreground">
                                Open an account with {joinNames(apiProviderNames)}, paste
                                the credentials here, and call using their numbers.
                                Quickest way to get started.
                            </p>
                        </div>
                        <Button
                            size="sm"
                            onClick={() => goToConfiguration({ add: true })}
                        >
                            Add provider
                        </Button>
                    </div>

                    <div className="rounded-lg border p-4 space-y-3">
                        <div className="space-y-1">
                            <h3 className="text-sm font-medium">Bring your own SIP</h3>
                            <p className="text-sm text-muted-foreground">
                                Already have a SIP trunk or a PBX? Point it at Dograh and
                                keep your existing carrier and numbers.
                                {sipConfig
                                    ? ` “${sipConfig.name}” is provisioned and waiting for your carrier details.`
                                    : ""}
                            </p>
                            {sipConfig && blockedReason && (
                                <p className="text-sm text-amber-600 dark:text-amber-500">
                                    {blockedReason}
                                </p>
                            )}
                        </div>
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                                goToConfiguration(
                                    sipConfig ? { configId: sipConfig.id } : { add: true },
                                )
                            }
                        >
                            {sipConfig ? "Set up SIP" : "Add SIP connection"}
                        </Button>
                    </div>
                </div>

                <DialogFooter>
                    <Button variant="ghost" onClick={() => onOpenChange(false)}>
                        Do it Later
                    </Button>
                </DialogFooter>
            </>
        );
    };

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
                                    {config.is_default_outbound ? " - default" : ""}
                                    {!isCallable(config) ? " - setup incomplete" : ""}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    {selectedConfigBlocked && (
                        <p className="text-xs text-amber-600 dark:text-amber-500">
                            {selectedConfig?.inactive
                                ? "This configuration is disabled after repeated connection failures."
                                : selectedConfig?.outbound_blocked_reason ??
                                  "This configuration is not ready for outbound calls."}{" "}
                            <button
                                type="button"
                                className="underline"
                                onClick={() =>
                                    goToConfiguration({ configId: selectedConfig?.id })
                                }
                            >
                                {selectedConfig?.inactive ? "Open configuration" : "Finish setup"}
                            </button>
                        </p>
                    )}
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
                                        {phone.label ? `${phone.label} - ${phone.address}` : phone.address}
                                        {phone.is_default_caller_id ? " - default" : ""}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    ) : selectedConfigBlocked ? (
                        // Never claim a fallback here: providers that require a
                        // caller ID reject the call outright when none exists.
                        <div className="text-xs text-amber-600 dark:text-amber-500">
                            No phone numbers in this configuration.
                        </div>
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
                            disabled={callLoading || !phoneNumber || selectedConfigBlocked}
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
                    : needsPhoneService
                        ? renderConnectPhoneService()
                        : renderPhoneCallForm()
                }
            </DialogContent>
        </Dialog>
    );
};
