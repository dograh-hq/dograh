"use client";

import {
  BadgeCheck,
  CheckCircle2,
  Circle,
  ExternalLink,
  Info,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import {
  getKycStatus,
  type KycStatus,
  submitKycFinal,
  submitKycStep1,
  submitKycStep2,
  submitKycStep3,
  submitKycStep4,
} from "@/lib/kyc";

type StepNumber = 1 | 2 | 3 | 4 | 5;

function deriveActiveStep(status: KycStatus, isBusiness: boolean): StepNumber {
  if (!status.kyc_status) return 1;
  if (!status.pan_verified) return 2;
  if (!status.aadhaar_verified) return 3;
  if (isBusiness && !status.gst_verified) return 4;
  return 5;
}

function VerificationBadge({
  label,
  verified,
}: {
  label: string;
  verified: boolean | null | undefined;
}) {
  return (
    <Badge variant={verified ? "default" : "secondary"} className="gap-1">
      {verified ? (
        <CheckCircle2 className="h-3 w-3" />
      ) : (
        <Circle className="h-3 w-3" />
      )}
      {label} {verified ? "verified" : "pending"}
    </Badge>
  );
}

export default function KycPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const hasFetched = useRef(false);

  const [status, setStatus] = useState<KycStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeStep, setActiveStep] = useState<StepNumber | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Step 1 form
  const [accountType, setAccountType] = useState<"individual" | "business">(
    "individual",
  );
  const [businessName, setBusinessName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [billingAddress, setBillingAddress] = useState("");
  const [termsAccepted, setTermsAccepted] = useState(false);

  // Step 2 form
  const [panHolderName, setPanHolderName] = useState("");
  const [panNumber, setPanNumber] = useState("");

  // Step 4 form
  const [gstNumber, setGstNumber] = useState("");

  const isBusiness =
    status?.account_type === "business" || accountType === "business";

  const fetchStatus = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const token = await getAccessToken();
        const next = await getKycStatus(token);
        setStatus(next);
        if (next.account_type === "business" || next.account_type === "individual") {
          setAccountType(next.account_type);
        }
        setActiveStep((current) =>
          current === null
            ? deriveActiveStep(next, next.account_type === "business")
            : current,
        );
        return next;
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Failed to load KYC status",
        );
        return null;
      } finally {
        setLoading(false);
        if (showSpinner) setRefreshing(false);
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    fetchStatus();
  }, [authLoading, user, fetchStatus]);

  const refreshAfterStep = useCallback(async () => {
    const next = await fetchStatus();
    if (next) {
      setActiveStep(
        deriveActiveStep(next, next.account_type === "business" || isBusiness),
      );
    } else {
      setActiveStep((current) =>
        current && current < 5 ? ((current + 1) as StepNumber) : current,
      );
    }
  }, [fetchStatus, isBusiness]);

  const runStep = async (action: () => Promise<unknown>) => {
    setSubmitting(true);
    try {
      await action();
      await refreshAfterStep();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Request failed");
    } finally {
      setSubmitting(false);
    }
  };

  const onSubmitStep1 = () =>
    runStep(async () => {
      const token = await getAccessToken();
      const res = await submitKycStep1(token, {
        term_and_condition: termsAccepted,
        account_type: accountType,
        ...(accountType === "business" ? { business_name: businessName } : {}),
        full_name: fullName,
        email,
        phone,
        billing_address: billingAddress,
      });
      toast.success(res.message || "Details registered");
    });

  const onSubmitStep2 = () =>
    runStep(async () => {
      const token = await getAccessToken();
      const res = await submitKycStep2(token, {
        pan_holder_name: panHolderName,
        pan_number: panNumber.trim().toUpperCase(),
      });
      toast.success(res.message || "PAN verified");
    });

  const onStartAadhaar = () =>
    runStep(async () => {
      const token = await getAccessToken();
      const res = await submitKycStep3(token, {
        redirect_url:
          typeof window !== "undefined" ? window.location.href : undefined,
      });
      const redirectUrl = res.data?.redirect_url;
      if (typeof redirectUrl === "string" && redirectUrl) {
        window.open(redirectUrl, "_blank", "noopener,noreferrer");
        toast.success(
          "Aadhaar verification opened in a new tab. Finish it there, then refresh the status here.",
        );
      } else {
        toast.success(res.message || "Aadhaar verification initiated");
      }
    });

  const onSubmitStep4 = () =>
    runStep(async () => {
      const token = await getAccessToken();
      const res = await submitKycStep4(token, {
        gst_number: gstNumber.trim().toUpperCase(),
      });
      toast.success(res.message || "GST verified");
    });

  const onFinalSubmit = () =>
    runStep(async () => {
      const token = await getAccessToken();
      const res = await submitKycFinal(token);
      toast.success(res.message || "KYC submitted for review");
    });

  const steps: {
    number: StepNumber;
    title: string;
    done: boolean;
  }[] = [
    { number: 1, title: "Account details", done: Boolean(status?.kyc_status) },
    { number: 2, title: "PAN verification", done: Boolean(status?.pan_verified) },
    {
      number: 3,
      title: "Aadhaar verification",
      done: Boolean(status?.aadhaar_verified),
    },
    ...(isBusiness
      ? [
          {
            number: 4 as StepNumber,
            title: "GST verification",
            done: Boolean(status?.gst_verified),
          },
        ]
      : []),
    { number: 5, title: "Submit for review", done: Boolean(status?.is_complete) },
  ];

  const readyForFinalSubmit =
    Boolean(status?.pan_verified) &&
    Boolean(status?.aadhaar_verified) &&
    (!isBusiness || Boolean(status?.gst_verified));

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto max-w-3xl px-4 py-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="mb-2 flex items-center gap-2 text-3xl font-bold">
              <ShieldCheck className="h-7 w-7" /> KYC verification
            </h1>
            <p className="text-muted-foreground">
              Complete KYC to activate calling on your number. Indian telephony
              regulations require identity verification before calls can be
              placed.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchStatus(true)}
            disabled={loading || refreshing}
          >
            <RefreshCw
              className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
            />
            Refresh status
          </Button>
        </div>

        {loading ? (
          <div className="grid gap-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : !status || !status.enabled ? (
          <Card>
            <CardHeader>
              <CardTitle>KYC not configured</CardTitle>
              <CardDescription>
                The telephony partner credentials for KYC are not configured on
                this deployment. Ask your administrator to set the VoiceLink
                reseller credentials on the API service.
              </CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <div className="space-y-6">
            <div className="flex flex-wrap items-center gap-2">
              <VerificationBadge label="PAN" verified={status.pan_verified} />
              <VerificationBadge
                label="Aadhaar"
                verified={status.aadhaar_verified}
              />
              {isBusiness && (
                <VerificationBadge label="GST" verified={status.gst_verified} />
              )}
              <Badge
                variant={status.is_complete ? "default" : "outline"}
                className="gap-1"
              >
                <BadgeCheck className="h-3 w-3" />
                {status.is_complete ? "KYC complete" : "KYC incomplete"}
              </Badge>
              {status.kyc_status && (
                <Badge variant="outline">Status: {status.kyc_status}</Badge>
              )}
            </div>

            {status.has_voicelink_config && !status.client_id_configured && (
              <div className="flex items-start gap-3 rounded-md border bg-muted/50 p-4 text-sm text-muted-foreground">
                <Info className="mt-0.5 h-4 w-4 shrink-0" />
                <p>
                  No VoiceLink client ID is set on your telephony configuration,
                  so KYC actions apply to the reseller account itself. To scope
                  KYC to this account, add the optional <code>Client ID</code>{" "}
                  credential to your VoiceLink telephony configuration.
                </p>
              </div>
            )}

            <div className="space-y-3">
              {steps.map((step) => {
                const isActive = activeStep === step.number;
                return (
                  <Card
                    key={step.number}
                    className={isActive ? "border-foreground/30" : undefined}
                  >
                    <CardHeader
                      className="cursor-pointer py-4"
                      onClick={() =>
                        setActiveStep(isActive ? null : step.number)
                      }
                    >
                      <div className="flex items-center gap-3">
                        <span
                          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-sm font-medium ${
                            step.done
                              ? "border-transparent bg-foreground text-background"
                              : isActive
                                ? "border-foreground"
                                : "border-border text-muted-foreground"
                          }`}
                        >
                          {step.done ? (
                            <CheckCircle2 className="h-4 w-4" />
                          ) : (
                            steps.findIndex((s) => s.number === step.number) + 1
                          )}
                        </span>
                        <CardTitle className="text-base">{step.title}</CardTitle>
                        {step.done && (
                          <Badge variant="secondary" className="ml-auto">
                            Done
                          </Badge>
                        )}
                      </div>
                    </CardHeader>

                    {isActive && (
                      <CardContent className="space-y-4 pt-0">
                        {step.number === 1 && (
                          <>
                            <div className="space-y-2">
                              <Label>Account type</Label>
                              <RadioGroup
                                value={accountType}
                                onValueChange={(v) =>
                                  setAccountType(v as "individual" | "business")
                                }
                                className="flex gap-6"
                              >
                                <div className="flex items-center gap-2">
                                  <RadioGroupItem
                                    value="individual"
                                    id="kyc-individual"
                                  />
                                  <Label htmlFor="kyc-individual">
                                    Individual
                                  </Label>
                                </div>
                                <div className="flex items-center gap-2">
                                  <RadioGroupItem
                                    value="business"
                                    id="kyc-business"
                                  />
                                  <Label htmlFor="kyc-business">Business</Label>
                                </div>
                              </RadioGroup>
                            </div>
                            {accountType === "business" && (
                              <div className="space-y-2">
                                <Label htmlFor="kyc-business-name">
                                  Business name
                                </Label>
                                <Input
                                  id="kyc-business-name"
                                  value={businessName}
                                  onChange={(e) =>
                                    setBusinessName(e.target.value)
                                  }
                                  placeholder="Auto4You Pvt Ltd"
                                />
                              </div>
                            )}
                            <div className="grid gap-4 sm:grid-cols-2">
                              <div className="space-y-2">
                                <Label htmlFor="kyc-full-name">Full name</Label>
                                <Input
                                  id="kyc-full-name"
                                  value={fullName}
                                  onChange={(e) => setFullName(e.target.value)}
                                />
                              </div>
                              <div className="space-y-2">
                                <Label htmlFor="kyc-email">Email</Label>
                                <Input
                                  id="kyc-email"
                                  type="email"
                                  value={email}
                                  onChange={(e) => setEmail(e.target.value)}
                                />
                              </div>
                              <div className="space-y-2 sm:col-span-2">
                                <Label htmlFor="kyc-phone">Phone</Label>
                                <Input
                                  id="kyc-phone"
                                  value={phone}
                                  onChange={(e) => setPhone(e.target.value)}
                                  placeholder="9999999999"
                                />
                              </div>
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor="kyc-billing-address">
                                Billing address
                              </Label>
                              <Textarea
                                id="kyc-billing-address"
                                value={billingAddress}
                                onChange={(e) =>
                                  setBillingAddress(e.target.value)
                                }
                                rows={3}
                              />
                            </div>
                            <div className="flex items-start gap-2">
                              <Checkbox
                                id="kyc-terms"
                                checked={termsAccepted}
                                onCheckedChange={(checked) =>
                                  setTermsAccepted(checked === true)
                                }
                              />
                              <Label
                                htmlFor="kyc-terms"
                                className="text-sm font-normal leading-snug text-muted-foreground"
                              >
                                I accept the telephony partner&apos;s terms and
                                conditions for KYC verification.
                              </Label>
                            </div>
                            <Button
                              onClick={onSubmitStep1}
                              disabled={
                                submitting ||
                                !termsAccepted ||
                                !fullName ||
                                !email ||
                                !phone ||
                                !billingAddress ||
                                (accountType === "business" && !businessName)
                              }
                            >
                              Save details
                            </Button>
                          </>
                        )}

                        {step.number === 2 && (
                          <>
                            <div className="grid gap-4 sm:grid-cols-2">
                              <div className="space-y-2">
                                <Label htmlFor="kyc-pan-holder">
                                  PAN holder name
                                </Label>
                                <Input
                                  id="kyc-pan-holder"
                                  value={panHolderName}
                                  onChange={(e) =>
                                    setPanHolderName(e.target.value)
                                  }
                                  placeholder="Name exactly as on the PAN card"
                                />
                              </div>
                              <div className="space-y-2">
                                <Label htmlFor="kyc-pan-number">
                                  PAN number
                                </Label>
                                <Input
                                  id="kyc-pan-number"
                                  value={panNumber}
                                  onChange={(e) => setPanNumber(e.target.value)}
                                  placeholder="ABCDE1234F"
                                />
                              </div>
                            </div>
                            <Button
                              onClick={onSubmitStep2}
                              disabled={
                                submitting || !panHolderName || !panNumber
                              }
                            >
                              Verify PAN
                            </Button>
                          </>
                        )}

                        {step.number === 3 && (
                          <>
                            <p className="text-sm text-muted-foreground">
                              Aadhaar is verified through DigiLocker. A secure
                              verification page opens in a new tab — finish it
                              there, then come back and refresh the status.
                            </p>
                            <div className="flex flex-wrap gap-2">
                              <Button onClick={onStartAadhaar} disabled={submitting}>
                                <ExternalLink className="mr-2 h-4 w-4" />
                                Start Aadhaar verification
                              </Button>
                              <Button
                                variant="outline"
                                onClick={() => fetchStatus(true)}
                                disabled={refreshing}
                              >
                                <RefreshCw
                                  className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
                                />
                                Refresh status
                              </Button>
                            </div>
                          </>
                        )}

                        {step.number === 4 && (
                          <>
                            <div className="space-y-2">
                              <Label htmlFor="kyc-gst">GST number</Label>
                              <Input
                                id="kyc-gst"
                                value={gstNumber}
                                onChange={(e) => setGstNumber(e.target.value)}
                                placeholder="22AAAAA0000A1Z5"
                              />
                            </div>
                            <Button
                              onClick={onSubmitStep4}
                              disabled={submitting || !gstNumber}
                            >
                              Verify GST
                            </Button>
                          </>
                        )}

                        {step.number === 5 && (
                          <>
                            <p className="text-sm text-muted-foreground">
                              {status.is_complete
                                ? "Your KYC is complete. Calling is activated on your number."
                                : readyForFinalSubmit
                                  ? "All verifications are done — submit your KYC for final review."
                                  : "Finish the verification steps above before submitting."}
                            </p>
                            {!status.is_complete && (
                              <Button
                                onClick={onFinalSubmit}
                                disabled={submitting || !readyForFinalSubmit}
                              >
                                Submit KYC
                              </Button>
                            )}
                          </>
                        )}
                      </CardContent>
                    )}
                  </Card>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
