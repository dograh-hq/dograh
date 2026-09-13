"use client";

import 'react-international-phone/style.css';

import { Clock, Loader2, PhoneOff } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
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
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import { useUserConfig } from "@/context/UserConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

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
    const { getAccessToken } = useAuth();
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

    // WhatsApp outbound permission state
    const [waPermissionStatus, setWaPermissionStatus] = useState<string>("idle");
    const [waHoursRemaining, setWaHoursRemaining] = useState<number | null>(null);
    const [waRestrictedReason, setWaRestrictedReason] = useState<string | null>(null);
    const [waDeliveryError, setWaDeliveryError] = useState<string | null>(null);
    const [waCanRequest, setWaCanRequest] = useState<boolean>(true);
    const [waRequestLimitReason, setWaRequestLimitReason] = useState<string | null>(null);
    // The recipient the current WhatsApp permission status describes, as
    // `configId|phone`. Consent is per recipient, so a status is only ever
    // allowed to gate a call to the exact recipient it was fetched for.
    const [waPermissionFor, setWaPermissionFor] = useState<string | null>(null);
    // Bumped whenever the recipient changes or a new check starts, so a reply
    // belonging to a previous recipient can be recognised and dropped.
    const waRequestSeqRef = useRef(0);
    const waAbortRef = useRef<AbortController | null>(null);
    const [requestingWaPermission, setRequestingWaPermission] = useState(false);
    const [customWaMessage, setCustomWaMessage] = useState<string>("");
    const [showCustomWaMessage, setShowCustomWaMessage] = useState<boolean>(false);

    // Active call live tracking state
    const [activeRunId, setActiveRunId] = useState<number | null>(null);
    const [callStatus, setCallStatus] = useState<"idle" | "calling" | "connected" | "ended" | "failed">("idle");
    const [callDuration, setCallDuration] = useState<number>(0);
    const [endingCall, setEndingCall] = useState<boolean>(false);
    // Set when /end-call returns 200 but the provider never confirmed the
    // hang-up. The backend preserves the call's identity for exactly this
    // case, so keep a way to re-issue it rather than hiding the button.
    const [providerHangupUnconfirmed, setProviderHangupUnconfirmed] = useState<boolean>(false);

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
            setActiveRunId(null);
            setCallStatus("idle");
            setCallDuration(0);
            setEndingCall(false);
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
    const isWhatsApp = selectedConfig?.provider === "whatsapp";
    const selectedConfigBlocked =
        selectedConfig !== undefined && !isCallable(selectedConfig);

    // Permission is only ever evidence about the recipient it was fetched for,
    // so the gate matches the status against the recipient on screen right now.
    const waPermissionKey = `${selectedConfigId}|${phoneNumber.trim()}`;
    const waPermissionGranted =
        waPermissionStatus === "granted" && waPermissionFor === waPermissionKey;

    const callIsActive = callStatus === "calling" || callStatus === "connected";
    // Also true once /end-call comes back 200 with the provider unconfirmed:
    // the run is closed on our side, but the carrier leg may still be live,
    // and End Call is the only control that can re-issue the hangup.
    const dismissalBlocked = callIsActive || providerHangupUnconfirmed;

    /**
     * Single gate for every way this dialog can be dismissed.
     *
     * While a call is live the footer deliberately offers no Close — End Call
     * is the only way out, because `activeRunId` lives in this component and
     * nothing else in the app can hang the call up once it is gone. Escape,
     * a click on the overlay and the corner X all reach us through Radix's
     * `onOpenChange`, so honouring that same rule here (rather than clearing
     * state on close) keeps the one escape hatch consistent instead of adding
     * a second, silent one that strands the call on the provider. The same
     * applies while `providerHangupUnconfirmed` is set: the dialog closing
     * (or "Call Again" resetting it) is just as capable of stranding the call
     * as closing it mid-ring, so it gates every dismissal path here too.
     *
     * Returns whether the dialog actually closed.
     */
    const requestClose = useCallback(() => {
        if (dismissalBlocked) {
            setCallError(
                "End the call before closing. Closing now would leave the call running on the provider with no way to hang it up from here.",
            );
            return false;
        }
        onOpenChange(false);
        return true;
    }, [dismissalBlocked, onOpenChange]);

    const nonSipConfigs = telephonyConfigs.filter(
        (config) => config.connectivity !== "sip" && !config.inactive,
    );
    const hasPendingOutbound = telephonyConfigs.some(
        (config) =>
            !config.inactive &&
            config.is_ready_for_outbound === false,
    );
    const needsPhoneService =
        needsConfiguration === true ||
        telephonyConfigs.length === 0 ||
        (hasPendingOutbound && !telephonyConfigs.some(isCallable) && nonSipConfigs.length === 0);

    /**
     * Drop whatever we knew about WhatsApp permission and abandon any check
     * still in flight.
     *
     * Consent is granted per recipient, so the moment the recipient or the
     * configuration changes the previous answer stops being evidence about the
     * new one. Bumping the sequence number orphans the in-flight reply as well,
     * so a slow response for the previous recipient can never land on the
     * current one.
     */
    const invalidateWaPermission = useCallback((nextStatus: string) => {
        waRequestSeqRef.current += 1;
        waAbortRef.current?.abort();
        waAbortRef.current = null;
        setWaPermissionStatus(nextStatus);
        setWaPermissionFor(null);
        setWaHoursRemaining(null);
        setWaRestrictedReason(null);
        setWaDeliveryError(null);
        setWaCanRequest(true);
        setWaRequestLimitReason(null);
    }, []);

    const checkWhatsAppPermissionNow = useCallback(async (phone: string, configId: string) => {
        const raw = phone.trim();
        if (!raw || raw.length < 8 || !configId) return;

        // This request owns the permission state only until a newer one (or an
        // invalidation) starts. Everything below is guarded on that.
        waRequestSeqRef.current += 1;
        const seq = waRequestSeqRef.current;
        const isCurrent = () => waRequestSeqRef.current === seq;

        waAbortRef.current?.abort();
        const controller = new AbortController();
        waAbortRef.current = controller;

        const permissionKey = `${configId}|${raw}`;
        setWaPermissionStatus("checking");
        setWaPermissionFor(null);
        try {
            const token = await getAccessToken();
            if (!isCurrent()) return;
            const headers: Record<string, string> = token
                ? { Authorization: `Bearer ${token}` }
                : {};
            const res = await fetch(
                `/api/v1/telephony/whatsapp/permissions/check?telephony_configuration_id=${configId}&recipient_phone_number=${encodeURIComponent(raw)}`,
                { headers, signal: controller.signal }
            );
            if (!isCurrent()) return;
            if (!res.ok) {
                setWaPermissionStatus("not_requested");
                setWaPermissionFor(permissionKey);
                return;
            }
            const data = await res.json().catch(() => ({}));
            if (!isCurrent()) return;
            setWaPermissionFor(permissionKey);
            if (data.restricted_country) {
                setWaPermissionStatus("restricted");
                setWaRestrictedReason(data.restriction_reason);
            } else if (data.can_call) {
                setWaPermissionStatus("granted");
                setWaHoursRemaining(data.hours_remaining ?? null);
                setWaDeliveryError(null);
                setWaCanRequest(false);
                setWaRequestLimitReason(null);
            } else {
                setWaPermissionStatus(data.status || "not_requested");
                setWaHoursRemaining(data.hours_remaining ?? null);
                setWaDeliveryError(data.delivery_error || null);
                setWaCanRequest(data.can_request_permission ?? true);
                setWaRequestLimitReason(data.request_limit_reason || null);
            }
        } catch {
            if (!isCurrent()) return;
            setWaPermissionStatus("not_requested");
            setWaPermissionFor(permissionKey);
        } finally {
            if (waAbortRef.current === controller) {
                waAbortRef.current = null;
            }
        }
    }, [getAccessToken]);

    // Check WhatsApp call permission when number or config changes.
    //
    // The status is invalidated synchronously on every change, before the
    // debounce even starts: leaving a stale "granted" on screen for 500ms is
    // long enough for a user to press Start Call and place an unconsented call
    // to the new recipient.
    useEffect(() => {
        if (!open || !isWhatsApp || !selectedConfigId) {
            invalidateWaPermission("idle");
            return;
        }

        const raw = phoneNumber.trim();
        if (!raw || raw.length < 8) {
            invalidateWaPermission("idle");
            return;
        }

        invalidateWaPermission("checking");

        const timer = setTimeout(() => {
            void checkWhatsAppPermissionNow(raw, selectedConfigId);
        }, 500);

        return () => {
            clearTimeout(timer);
        };
    }, [open, isWhatsApp, selectedConfigId, phoneNumber, checkWhatsAppPermissionNow, invalidateWaPermission]);

    // Load saved default permission message from configuration
    useEffect(() => {
        if (!open || !isWhatsApp || !selectedConfigId) {
            setCustomWaMessage("");
            setShowCustomWaMessage(false);
            return;
        }
        let cancelled = false;
        (async () => {
            try {
                const token = await getAccessToken();
                const res = await fetch(`/api/v1/organizations/telephony-configs/${selectedConfigId}`, {
                    headers: token ? { Authorization: `Bearer ${token}` } : {},
                });
                if (!res.ok || cancelled) return;
                const data = await res.json();
                if (cancelled) return;
                const saved = data?.credentials?.default_permission_message;
                if (saved) {
                    setCustomWaMessage(saved);
                }
            } catch {
                // Ignore failure to load config detail
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [open, isWhatsApp, selectedConfigId, getAccessToken]);

    const handleRequestWhatsAppPermission = async () => {
        if (!selectedConfigId || !phoneNumber) return;
        setRequestingWaPermission(true);
        setCallError(null);
        try {
            const token = await getAccessToken();
            const headers: Record<string, string> = {
                'Content-Type': 'application/json',
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            };
            const payload: Record<string, string | number> = {
                telephony_configuration_id: Number(selectedConfigId),
                recipient_phone_number: phoneNumber,
            };
            if (customWaMessage.trim()) {
                payload.body_text = customWaMessage.trim();
            }
            const res = await fetch('/api/v1/telephony/whatsapp/permissions/request', {
                method: 'POST',
                headers,
                body: JSON.stringify(payload),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(
                    data.detail || `Failed to send WhatsApp call permission request (${res.status} ${res.statusText || ""})`
                );
            }
            // Supersede any check still in flight so its (pre-request) answer
            // cannot overwrite the pending state we just created.
            waRequestSeqRef.current += 1;
            waAbortRef.current?.abort();
            waAbortRef.current = null;
            setWaPermissionStatus("pending");
            setWaPermissionFor(`${selectedConfigId}|${phoneNumber.trim()}`);
            setWaHoursRemaining(168);
            setWaDeliveryError(null);
        } catch (err) {
            setCallError(
                err instanceof Error ? err.message : "Failed to send permission request",
            );
        } finally {
            setRequestingWaPermission(false);
        }
    };

    const goToConfiguration = (target?: { configId?: number; add?: boolean }) => {
        if (!requestClose()) return;
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

    const handleResetCall = () => {
        setActiveRunId(null);
        setCallStatus("idle");
        setCallDuration(0);
        setEndingCall(false);
        setCallSuccessMsg(null);
        setCallError(null);
    };

    const formatDuration = (seconds: number) => {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
    };

    const handleEndCall = async () => {
        if (!activeRunId) return;
        setEndingCall(true);
        setCallError(null);
        // Do NOT clear providerHangupUnconfirmed here. It is the flag that
        // keeps this dialog un-dismissable and the End Call button visible
        // after a prior partial hangup; a retry that later throws (network
        // error, timeout) must leave it exactly as it was, not reset to
        // false, or the button disappears and the carrier leg is stranded
        // with no way to re-issue the hangup. The success path below is the
        // only place allowed to change it, since only a fresh response can
        // tell us the provider's actual state.
        try {
            const token = await getAccessToken();
            const headers: Record<string, string> = {
                "Content-Type": "application/json",
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            };
            const res = await fetch(`/api/v1/telephony/runs/${activeRunId}/end-call`, {
                method: "POST",
                headers,
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                const detail =
                    typeof data?.detail === "string" ? data.detail : null;
                throw new Error(
                    detail ||
                        `Failed to end the call (${res.status}${res.statusText ? ` ${res.statusText}` : ""}).`,
                );
            }
            // A 200 does not necessarily mean the carrier hung up: the backend
            // reports "partial" when it tore down our side but the provider
            // never confirmed. Mark the run ended (it is closed server-side)
            // but tell the user the recipient may still be connected.
            const body = await res.json().catch(() => ({}));
            setCallStatus("ended");
            const unconfirmed = body?.provider_terminated === false;
            setProviderHangupUnconfirmed(unconfirmed);
            if (unconfirmed) {
                setCallError(
                    typeof body?.message === "string"
                        ? body.message
                        : "The call was closed on our side, but the provider did not confirm the hang-up.",
                );
            }
        } catch (err: unknown) {
            console.error("Failed to end call:", err);
            // The provider call is very likely still live, so keep callStatus
            // where it is: polling continues and End Call stays available to
            // retry. Marking it "ended" here would hide a call nobody can now
            // hang up.
            setCallError(
                `${err instanceof Error ? err.message : "Failed to end the call."} The call may still be in progress — try again.`,
            );
        } finally {
            setEndingCall(false);
        }
    };

    // Poll active call status every 1 second when calling or connected
    useEffect(() => {
        if (!open || !activeRunId || (callStatus !== "calling" && callStatus !== "connected")) {
            return;
        }

        let cancelled = false;
        const pollInterval = setInterval(async () => {
            try {
                const token = await getAccessToken();
                const headers: Record<string, string> = {
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                };
                const res = await fetch(`/api/v1/telephony/runs/${activeRunId}/call-status`, {
                    headers,
                });
                if (cancelled || !res.ok) return;
                const data = await res.json();
                if (cancelled) return;

                if (data.status === "connected") {
                    setCallStatus("connected");
                    if (typeof data.duration_seconds === "number") {
                        setCallDuration(data.duration_seconds);
                    }
                } else if (data.status === "ringing") {
                    setCallStatus("calling");
                    setCallDuration(0);
                } else if (data.status === "failed") {
                    setCallStatus("failed");
                    if (data.error) setCallError(data.error);
                } else if (data.status === "completed" || data.is_completed) {
                    setCallStatus("ended");
                    if (typeof data.duration_seconds === "number") {
                        setCallDuration(data.duration_seconds);
                    }
                    if (data.error) setCallError(data.error);
                }
            } catch (err) {
                console.error("Error polling call status:", err);
            }
        }, 1000);

        return () => {
            cancelled = true;
            clearInterval(pollInterval);
        };
    }, [open, activeRunId, callStatus, getAccessToken]);

    // Timer increment while connected
    useEffect(() => {
        if (callStatus !== "connected") return;
        const timer = setInterval(() => {
            setCallDuration((prev) => prev + 1);
        }, 1000);
        return () => clearInterval(timer);
    }, [callStatus]);

    const handleStartCall = async () => {
        // A new call supersedes any unresolved hang-up from the previous one.
        setProviderHangupUnconfirmed(false);
        if (isWhatsApp) {
            if (waPermissionStatus === "restricted") {
                setCallError(
                    waRestrictedReason ||
                        "Business-Initiated WhatsApp Calls are not supported in this country.",
                );
                return;
            }
            if (!waPermissionGranted) {
                setCallError(
                    "Permission to call this WhatsApp number has not been granted by the recipient. Please send a permission request first.",
                );
                return;
            }
        }
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
                setCallStatus("failed");
            } else {
                const rawData = response.data as
                    | { message?: string; workflow_run_id?: number }
                    | undefined;
                const msg = rawData?.message || "Call initiated successfully!";
                const runId = rawData?.workflow_run_id;
                if (runId) {
                    setActiveRunId(runId);
                }
                setCallStatus("calling");
                setCallDuration(0);
                setCallSuccessMsg(typeof msg === "string" ? msg : JSON.stringify(msg));
            }
        } catch (err: unknown) {
            setCallError(err instanceof Error ? err.message : "Failed to initiate call");
            setCallStatus("failed");
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
                    <Button variant="ghost" onClick={() => requestClose()}>
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
                        <SelectTrigger id="telephony-config" className="w-full max-w-full overflow-hidden min-w-0 [&>span]:truncate [&>span]:min-w-0">
                            <SelectValue placeholder="Select a configuration" />
                        </SelectTrigger>
                        <SelectContent>
                            {telephonyConfigs.map((config) => {
                                const statusSuffix = !isCallable(config)
                                    ? " - setup incomplete"
                                    : "";
                                const label = `${config.name} (${config.provider})${config.is_default_outbound ? " - default" : ""}${statusSuffix}`;
                                return (
                                    <SelectItem key={config.id} value={String(config.id)} title={label}>
                                        <span className="truncate max-w-[380px]">{label}</span>
                                    </SelectItem>
                                );
                            })}
                        </SelectContent>
                    </Select>
                    {selectedConfigBlocked ? (
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
                    ) : null}
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
                            <SelectTrigger id="from-phone-number" className="w-full max-w-full overflow-hidden min-w-0 [&>span]:truncate [&>span]:min-w-0">
                                <SelectValue placeholder="Select a phone number" />
                            </SelectTrigger>
                            <SelectContent>
                                {fromPhoneNumbers.map((phone) => {
                                    const label = `${phone.label ? `${phone.label} - ${phone.address}` : phone.address}${phone.is_default_caller_id ? " - default" : ""}`;
                                    return (
                                        <SelectItem key={phone.id} value={String(phone.id)} title={label}>
                                            <span className="truncate max-w-[380px]">{label}</span>
                                        </SelectItem>
                                    );
                                })}
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
                    className="w-full"
                    inputClassName="!w-full !bg-transparent !text-foreground !border-input"
                />
            )}
            <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground underline"
                onClick={() => { setSipMode(!sipMode); setPhoneNumber(""); setPhoneChanged(true); }}
            >
                {sipMode ? "Use phone number instead" : "Use SIP endpoint instead"}
            </button>
            {isWhatsApp && !selectedConfigBlocked && phoneNumber.trim().length >= 4 && (
                <div className="space-y-2 pt-1">
                    {waPermissionStatus === "restricted" && (
                        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-xs text-destructive">
                            <div className="font-semibold mb-0.5">Country Not Supported for WhatsApp Outbound Calls</div>
                            <div>{waRestrictedReason || "Business-Initiated WhatsApp Calls are restricted in this destination country."}</div>
                        </div>
                    )}
                    {waPermissionStatus === "checking" && (
                        <div className="flex items-center text-xs text-muted-foreground gap-2 py-1">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            Checking WhatsApp call permission with Meta...
                        </div>
                    )}
                    {waPermissionGranted && (
                        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-700 dark:text-emerald-400">
                            <div className="font-semibold mb-0.5 flex items-center gap-1.5">
                                <span>✓</span> WhatsApp Call Permission Granted
                            </div>
                            <div>
                                Recipient has granted call permission
                                {waHoursRemaining ? ` (valid for ~${Math.round(waHoursRemaining)}h)` : ""}.
                            </div>
                        </div>
                    )}
                    {waPermissionStatus === "pending" && (
                        <div className="rounded-md border border-blue-500/30 bg-blue-500/10 p-3 text-xs text-blue-700 dark:text-blue-400 space-y-2">
                            <div className="font-semibold mb-0.5">Permission Request Pending</div>
                            <div>
                                An interactive permission request was sent to this WhatsApp number.
                                Waiting for the recipient to accept on WhatsApp
                                {waHoursRemaining ? ` (request valid for ~${Math.round(waHoursRemaining)}h)` : ""}.
                            </div>
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-xs"
                                    onClick={() => checkWhatsAppPermissionNow(phoneNumber, selectedConfigId)}
                                >
                                    Re-check Status
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-xs bg-blue-500/20 border-blue-500/40 hover:bg-blue-500/30 text-blue-900 dark:text-blue-200"
                                    disabled={requestingWaPermission || waCanRequest === false}
                                    onClick={handleRequestWhatsAppPermission}
                                    title={waCanRequest === false ? (waRequestLimitReason || "Meta limit reached") : "Re-send permission request to recipient"}
                                >
                                    {requestingWaPermission ? (
                                        <>
                                            <Loader2 className="h-3 w-3 animate-spin mr-1.5" />
                                            Sending...
                                        </>
                                    ) : (
                                        "Re-send Request"
                                    )}
                                </Button>
                            </div>
                            {waCanRequest === false && waRequestLimitReason && (
                                <div className="text-[11px] text-muted-foreground italic">
                                    {waRequestLimitReason}
                                </div>
                            )}
                        </div>
                    )}
                    {waPermissionStatus === "delivery_failed" && (
                        <div className="rounded-md border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-700 dark:text-red-400 space-y-2">
                            <div className="font-semibold mb-0.5 flex items-center gap-1.5">
                                <span>⚠️</span> Permission Request Delivery Failed
                            </div>
                            <div>
                                {waDeliveryError ||
                                    "Permission request message failed to deliver because the 24-hour customer service window is closed."}
                            </div>
                            <div className="p-2 rounded bg-background/60 text-muted-foreground">
                                <strong>How to fix:</strong> Ask the recipient to send any WhatsApp message (e.g. &quot;Hi&quot;) to your business number first. This opens the 24-hour customer service window so permission requests can be delivered.
                            </div>
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-xs"
                                    disabled={requestingWaPermission || waCanRequest === false}
                                    onClick={handleRequestWhatsAppPermission}
                                >
                                    {requestingWaPermission ? (
                                        <>
                                            <Loader2 className="h-3 w-3 animate-spin mr-1.5" />
                                            Retrying...
                                        </>
                                    ) : (
                                        "Retry Sending Request"
                                    )}
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 text-xs"
                                    onClick={() => checkWhatsAppPermissionNow(phoneNumber, selectedConfigId)}
                                >
                                    Re-check Status
                                </Button>
                            </div>
                            {waCanRequest === false && waRequestLimitReason && (
                                <div className="text-[11px] text-muted-foreground italic">
                                    {waRequestLimitReason}
                                </div>
                            )}
                        </div>
                    )}
                    {waPermissionStatus === "token_expired" && (
                        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive space-y-2">
                            <div className="font-semibold mb-0.5 flex items-center gap-1.5">
                                <span>⚠️</span> WhatsApp Access Token Expired or Invalid
                            </div>
                            <div>
                                {waDeliveryError ||
                                    "The Meta access token for this WhatsApp telephony configuration has expired or is invalid."}
                            </div>
                            <div className="pt-1">
                                <button
                                    type="button"
                                    className="underline font-semibold hover:opacity-80"
                                    onClick={() => goToConfiguration({ configId: Number(selectedConfigId) })}
                                >
                                    Update Token in Telephony Configurations →
                                </button>
                            </div>
                        </div>
                    )}
                    {(waPermissionStatus === "not_requested" ||
                        waPermissionStatus === "no_permission" ||
                        waPermissionStatus === "revoked" ||
                        waPermissionStatus === "expired" ||
                        waPermissionStatus === "denied") && (
                        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-400 space-y-2">
                            <div className="font-semibold mb-0.5">
                                {waPermissionStatus === "expired"
                                    ? "WhatsApp Call Permission Expired"
                                    : waPermissionStatus === "denied"
                                    ? "WhatsApp Call Permission Denied"
                                    : waPermissionStatus === "revoked" || waPermissionStatus === "no_permission"
                                    ? "WhatsApp Call Permission Revoked / Not Granted"
                                    : "WhatsApp Call Permission Required"}
                            </div>
                            <div>
                                Meta requires recipient permission before placing an outbound WhatsApp call.
                                Send an interactive request to their WhatsApp to grant permission.
                            </div>
                            <div className="space-y-1.5 pt-0.5">
                                <button
                                    type="button"
                                    onClick={() => setShowCustomWaMessage(!showCustomWaMessage)}
                                    className="text-[11px] text-amber-800 dark:text-amber-300 underline hover:no-underline font-medium inline-block"
                                >
                                    {showCustomWaMessage ? "Hide message preview / customization" : "Customize message before sending"}
                                </button>
                                {showCustomWaMessage && (
                                    <div className="space-y-1 pt-1">
                                        <textarea
                                            value={customWaMessage}
                                            onChange={(e) => setCustomWaMessage(e.target.value)}
                                            placeholder="Leave empty to use global telephony configuration default message"
                                            rows={3}
                                            className="w-full text-xs p-2 rounded border bg-background text-foreground resize-y focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-amber-500"
                                        />
                                        <div className="text-[10px] text-muted-foreground">
                                            Leave empty to use the default message configured in Telephony Configuration.
                                        </div>
                                    </div>
                                )}
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs bg-amber-500/20 border-amber-500/40 hover:bg-amber-500/30 text-amber-900 dark:text-amber-200"
                                disabled={requestingWaPermission}
                                onClick={handleRequestWhatsAppPermission}
                            >
                                {requestingWaPermission ? (
                                    <>
                                        <Loader2 className="h-3 w-3 animate-spin mr-1.5" />
                                        Sending Request...
                                    </>
                                ) : (
                                    "Send Permission Request via WhatsApp"
                                )}
                            </Button>
                        </div>
                    )}
                </div>
            )}
            {callStatus !== "idle" && (
                <div className="rounded-lg border p-4 my-3 flex flex-col items-center justify-center space-y-2 bg-muted/20">
                    {callStatus === "calling" && (
                        <div className="flex flex-col items-center space-y-2 text-center py-1">
                            <div className="flex items-center gap-2">
                                <span className="relative flex h-3 w-3">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                                    <span className="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
                                </span>
                                <span className="text-sm font-medium text-amber-700 dark:text-amber-400">
                                    Calling {phoneNumber}...
                                </span>
                            </div>
                            <p className="text-xs text-muted-foreground">Waiting for recipient to answer...</p>
                        </div>
                    )}

                    {callStatus === "connected" && (
                        <div className="flex flex-col items-center space-y-2 text-center py-1">
                            <div className="flex items-center gap-2">
                                <span className="relative flex h-3 w-3">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                    <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                                </span>
                                <span className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                                    Call In Progress
                                </span>
                            </div>
                            <div className="flex items-center gap-2 text-3xl font-mono font-bold tracking-widest text-foreground py-1">
                                <Clock className="h-6 w-6 text-muted-foreground" />
                                <span>{formatDuration(callDuration)}</span>
                            </div>
                        </div>
                    )}

                    {callStatus === "ended" && (
                        <div className="flex flex-col items-center space-y-1 text-center py-1">
                            <div className="flex items-center gap-2">
                                <span className="inline-flex rounded-full h-2.5 w-2.5 bg-zinc-400"></span>
                                <span className="text-sm font-medium text-muted-foreground">
                                    Call Ended
                                </span>
                            </div>
                            <p className="text-xs font-mono text-muted-foreground">
                                Call duration: {formatDuration(callDuration)}
                            </p>
                        </div>
                    )}

                    {callStatus === "failed" && (
                        <div className="flex flex-col items-center space-y-1 text-center py-1">
                            <div className="flex items-center gap-2">
                                <span className="inline-flex rounded-full h-2.5 w-2.5 bg-destructive"></span>
                                <span className="text-sm font-medium text-destructive">
                                    Call Terminated / Failed
                                </span>
                            </div>
                        </div>
                    )}
                </div>
            )}
            <DialogFooter className="flex-col sm:flex-row gap-2">
                <Button
                    variant="outline"
                    disabled={dismissalBlocked}
                    title={
                        dismissalBlocked
                            ? "End the call before leaving this dialog"
                            : undefined
                    }
                    onClick={() => {
                        if (!requestClose()) return;
                        router.push('/telephony-configurations');
                    }}
                >
                    Configure Telephony
                </Button>
                <div className="flex gap-2 flex-1 justify-end">
                    {callStatus === "idle" && (
                        <DialogClose asChild>
                            <Button variant="outline">Cancel</Button>
                        </DialogClose>
                    )}
                    {callStatus === "idle" ? (
                        isWhatsApp && !waPermissionGranted ? (
                            <TooltipProvider>
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <span
                                            tabIndex={0}
                                            className="inline-block cursor-not-allowed"
                                        >
                                            <Button
                                                disabled
                                                className="pointer-events-none"
                                            >
                                                Start Call
                                            </Button>
                                        </span>
                                    </TooltipTrigger>
                                    <TooltipContent side="top" className="max-w-xs text-center">
                                        {waPermissionStatus === "restricted"
                                            ? waRestrictedReason || "WhatsApp outbound calling is not supported in this destination country."
                                            : "Recipient must grant call permission on WhatsApp before calling."}
                                    </TooltipContent>
                                </Tooltip>
                            </TooltipProvider>
                        ) : (
                            <Button
                                onClick={handleStartCall}
                                disabled={callLoading || !phoneNumber || selectedConfigBlocked}
                            >
                                {callLoading ? "Calling..." : "Start Call"}
                            </Button>
                        )
                    ) : (
                        <>
                            <Button
                                variant="outline"
                                onClick={handleResetCall}
                                disabled={dismissalBlocked}
                                title={
                                    dismissalBlocked
                                        ? "End the call before starting a new one"
                                        : undefined
                                }
                            >
                                Call Again
                            </Button>
                            {(callStatus === "calling" || callStatus === "connected" || providerHangupUnconfirmed) ? (
                                <Button
                                    variant="destructive"
                                    onClick={handleEndCall}
                                    disabled={endingCall}
                                >
                                    {endingCall ? (
                                        <>
                                            <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
                                            Ending...
                                        </>
                                    ) : (
                                        <>
                                            <PhoneOff className="h-4 w-4 mr-1.5" />
                                            End Call
                                        </>
                                    )}
                                </Button>
                            ) : (
                                <Button onClick={() => requestClose()}>
                                    Close
                                </Button>
                            )}
                        </>
                    )}
                </div>
            </DialogFooter>
            {callError && <div className="text-red-500 text-sm mt-2 break-all break-words max-w-full">{callError}</div>}
            {callSuccessMsg && callStatus === "idle" && <div className="text-green-600 text-sm mt-2 break-all break-words max-w-full">{callSuccessMsg}</div>}
        </>
    );

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) {
                    onOpenChange(true);
                    return;
                }
                requestClose();
            }}
        >
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
