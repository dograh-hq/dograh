"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet, saveTelephonyConfigurationApiV1OrganizationsTelephonyConfigPost } from "@/client/sdk.gen";
import type {
  CloudonixConfigurationRequest,
  CloudonixConfigurationResponse,
  TelephonyConfigurationResponse,
  TwilioConfigurationRequest,
  VobizConfigurationRequest,
  VonageConfigurationRequest
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAuth } from "@/lib/auth";

// TODO: Make UI provider-agnostic
interface TelephonyConfigForm {
  provider: string;
  // Twilio fields
  account_sid?: string;
  auth_token?: string;
  // Vonage fields
  application_id?: string;
  private_key?: string;
  api_key?: string;
  api_secret?: string;
  // Vobiz fields
  auth_id?: string;
  vobiz_auth_token?: string;
  // Cloudonix fields
  bearer_token?: string;
  domain_id?: string;
  // Common field
  from_number: string;
}

export default function ConfigureTelephonyPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const [isLoading, setIsLoading] = useState(false);
  const [hasExistingConfig, setHasExistingConfig] = useState(false);

  // Get returnTo parameter from URL
  const returnTo = searchParams.get("returnTo") || "/workflow";

  const {
    register,
    handleSubmit,
    formState: { errors },
    setValue,
    watch,
  } = useForm<TelephonyConfigForm>({
    defaultValues: {
      provider: "twilio",
    },
  });

  const selectedProvider = watch("provider");

  useEffect(() => {
    // Don't fetch config while auth is still loading
    if (authLoading || !user) {
      return;
    }

    // Fetch existing configuration with masked sensitive fields
    const fetchConfig = async () => {
      try {
        const accessToken = await getAccessToken();
        const response = await getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet({
          headers: { Authorization: `Bearer ${accessToken}` },
        });

        if (!response.error) {
          // Simple single provider config
          if (response.data?.twilio) {
            setHasExistingConfig(true);
            setValue("provider", "twilio");
            setValue("account_sid", response.data.twilio.account_sid);
            setValue("auth_token", response.data.twilio.auth_token);
            if (response.data.twilio.from_numbers?.length > 0) {
              setValue("from_number", response.data.twilio.from_numbers[0]);
            }
          } else if (response.data?.vonage) {
            setHasExistingConfig(true);
            setValue("provider", "vonage");
            setValue("application_id", response.data.vonage.application_id);
            setValue("private_key", response.data.vonage.private_key);
            setValue("api_key", response.data.vonage.api_key || "");
            setValue("api_secret", response.data.vonage.api_secret || "");
            if (response.data.vonage.from_numbers?.length > 0) {
              setValue("from_number", response.data.vonage.from_numbers[0]);
            }
          } else if (response.data?.vobiz) {
            setHasExistingConfig(true);
            setValue("provider", "vobiz");
            setValue("auth_id", response.data.vobiz.auth_id);
            setValue("vobiz_auth_token", response.data.vobiz.auth_token);
            if (response.data.vobiz.from_numbers?.length > 0) {
              setValue("from_number", response.data.vobiz.from_numbers[0]);
            }
          } else if ((response.data as TelephonyConfigurationResponse)?.cloudonix) {
            const cloudonixConfig = (response.data as TelephonyConfigurationResponse).cloudonix as CloudonixConfigurationResponse;
            setHasExistingConfig(true);
            setValue("provider", "cloudonix");
            setValue("bearer_token", cloudonixConfig.bearer_token);
            setValue("domain_id", cloudonixConfig.domain_id);
            if (cloudonixConfig.from_numbers?.length > 0) {
              setValue("from_number", cloudonixConfig.from_numbers[0]);
            }
          }
        }
      } catch (error) {
        console.error("Failed to fetch config:", error);
      }
    };

    fetchConfig();
  }, [setValue, getAccessToken, authLoading, user]);

  const onSubmit = async (data: TelephonyConfigForm) => {
    setIsLoading(true);

    try {
      const accessToken = await getAccessToken();

      // Build the request body based on provider
      let requestBody:
        | TwilioConfigurationRequest
        | VonageConfigurationRequest
        | VobizConfigurationRequest
        | CloudonixConfigurationRequest;

      if (data.provider === "twilio") {
        requestBody = {
          provider: data.provider,
          from_numbers: [data.from_number],
          account_sid: data.account_sid,
          auth_token: data.auth_token,
        } as TwilioConfigurationRequest;
      } else if (data.provider === "vonage") {
        requestBody = {
          provider: data.provider,
          from_numbers: [data.from_number],
          application_id: data.application_id,
          private_key: data.private_key,
          api_key: data.api_key || undefined,
          api_secret: data.api_secret || undefined,
        } as VonageConfigurationRequest;
      } else if (data.provider === "vobiz") {
        requestBody = {
          provider: data.provider,
          from_numbers: [data.from_number],
          auth_id: data.auth_id,
          auth_token: data.vobiz_auth_token,
        } as VobizConfigurationRequest;
      } else {
        // Cloudonix
        requestBody = {
          provider: data.provider,
          from_numbers: data.from_number ? [data.from_number] : [],
          bearer_token: data.bearer_token!,
          domain_id: data.domain_id!,
        } as CloudonixConfigurationRequest;
      }

      const response = await saveTelephonyConfigurationApiV1OrganizationsTelephonyConfigPost({
        headers: { Authorization: `Bearer ${accessToken}` },
        body: requestBody
      });

      if (response.error) {
        const errorMsg = typeof response.error === 'string'
          ? response.error
          : (response.error as { detail?: string })?.detail || "Failed to save configuration";
        throw new Error(errorMsg);
      }

      toast.success("Telephony configuration saved successfully");

      // Redirect back to the page that sent us here
      router.push(returnTo);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to save configuration"
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="container mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-2">Configure Telephony</h1>
        <p className="text-gray-600 mb-6">
          Set up your telephony provider to make phone calls
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
            <Card className="h-full">
              <CardHeader>
                <CardTitle>
                  {selectedProvider === "twilio"
                    ? "Twilio"
                    : selectedProvider === "vonage"
                    ? "Vonage"
                    : selectedProvider === "vobiz"
                    ? "Vobiz"
                    : "Cloudonix"}{" "}
                  Setup Guide
                </CardTitle>
                <CardDescription>
                  {selectedProvider === "cloudonix" ? (
                    <>
                      Cloudonix is an AI Connectivity platform, enabling you to connect Dograh to any SIP product or SIP Telephony Provider.<br/><br/>
                      <iframe
                        style={{ border: 0 }}
                        width="100%"
                        height="450"
                        src="https://www.youtube.com/embed/qLKX0F99jpU?si=a_sF9ijSJdV4OdG-"
                        allowFullScreen
                        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                      /><br/><br/>
                      Visit{" "}
                      <a
                        href="https://cockpit.cloudonix.io/onboarding?affiliate=DOGRAH"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline"
                      >
                        https://cloudonix.com
                      </a>{" "}
                      for more information about Cloudonix services and pricing.Visit{" "}
                      <a
                        href="https://developers.cloudonix.com"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline"
                      >
                        https://developers.cloudonix.com
                      </a>{" "}
                      for developer documentation and API reference.
                    </>
                  ) : selectedProvider === "vobiz" ? (
                    <>
                      Vobiz is a telephony provider. Visit their documentation
                      for setup instructions.
                    </>
                  ) : (
                    <>
                      Watch this video to learn how to setup{" "}
                      {selectedProvider === "twilio" ? "Twilio" : "Vonage"}
                    </>
                  )}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {selectedProvider === "twilio" || selectedProvider === "vonage" ? (
                  <div className="aspect-video">
                    <iframe
                      style={{ border: 0 }}
                      width="100%"
                      height="100%"
                      src={
                        selectedProvider === "twilio"
                          ? "https://www.tella.tv/video/cmgbvzkrt00jk0clacu16blm3/embed?b=0&title=1&a=1&loop=0&t=0&muted=0&wt=0"
                          : "https://www.tella.tv/video/configuring-telephony-on-dograh-with-vonage-3wvo/embed?b=0&title=1&a=1&loop=0&t=0&muted=0&wt=0"
                      }
                      allowFullScreen
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    />
                  </div>
                ) : selectedProvider === "vobiz" ? (
                  <div className="space-y-4 text-sm">
                    <div>
                      <h4 className="font-semibold mb-2">Getting Started with Vobiz:</h4>
                      <ol className="list-decimal list-inside space-y-1 text-gray-600">
                        <li>Sign up for a Vobiz account</li>
                        <li>Get your Auth ID from the Vobiz dashboard</li>
                        <li>Generate an Auth Token</li>
                        <li>Configure phone numbers in your Vobiz account</li>
                        <li>Enter your credentials below</li>
                      </ol>
                    </div>
                    <div className="bg-blue-50 border border-blue-200 rounded p-3">
                      <p className="text-sm text-blue-800">
                        <strong>Note:</strong> Vobiz provides cloud-based telephony services
                        with global reach and competitive pricing.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4 text-sm">
                    <div>
                      <h4 className="font-semibold mb-2">Getting Started with Cloudonix:</h4>
                      <ol className="list-decimal list-inside space-y-1 text-gray-600">
                        <li>Sign up for a Cloudonix account at https://cloudonix.com</li>
                        <li>Create an <i>API token</i> for your Cloudonix domain</li>
                        <li>Configure your Cloudoinx <i>API Token</i> and <i>Cloudonix Domain Name</i> in Dograh</li>
                        <li>Configure an optional outbound phone number for your Dograh agent</li>
                      </ol>
                    </div>
                    <div className="bg-blue-50 border border-blue-200 rounded p-3">
                      <p className="text-sm text-blue-800">
                        <strong>Note:</strong> Cloudonix uses Bearer token
                        authentication and is fully TwiML-compatible for voice
                        applications.
                      </p>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
          <div>
            <form onSubmit={handleSubmit(onSubmit)}>
              <Card>
                <CardHeader>
                  <CardTitle>Provider Configuration</CardTitle>
                  <CardDescription>
                    Configure your telephony provider settings
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* Provider Selection */}
                  <div className="space-y-2">
                    <Label>Provider</Label>
                    <Select
                      value={selectedProvider}
                      onValueChange={(value) => setValue("provider", value)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="twilio">Twilio</SelectItem>
                        <SelectItem value="vonage">Vonage</SelectItem>
                        <SelectItem value="vobiz">Vobiz</SelectItem>
                        <SelectItem value="cloudonix">Cloudonix</SelectItem>
                      </SelectContent>
                    </Select>
                    {hasExistingConfig && (
                      <p className="text-sm text-amber-600">
                        ⚠️ Switching providers will require entering new credentials
                      </p>
                    )}
                  </div>

                  {/* Twilio-specific fields */}
                  {selectedProvider === "twilio" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="account_sid">Account SID</Label>
                        <Input
                          id="account_sid"
                          autoComplete="username"
                          placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                          {...register("account_sid", {
                            required: "Account SID is required",
                          })}
                        />
                        {errors.account_sid && (
                          <p className="text-sm text-red-500">
                            {errors.account_sid.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="auth_token">Auth Token</Label>
                        <Input
                          id="auth_token"
                          type="password"
                          autoComplete="current-password"
                          placeholder={
                            hasExistingConfig
                              ? "Leave masked to keep existing"
                              : "Enter your auth token"
                          }
                          {...register("auth_token", {
                            required: !hasExistingConfig
                              ? "Auth token is required"
                              : false,
                          })}
                        />
                        {errors.auth_token && (
                          <p className="text-sm text-red-500">
                            {errors.auth_token.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="from_number">From Phone Number</Label>
                        <Input
                          id="from_number"
                          autoComplete="tel"
                          placeholder="+1234567890"
                          {...register("from_number", {
                            required: "Phone number is required",
                            pattern: {
                              value: /^\+[1-9]\d{1,14}$/,
                              message:
                                "Enter a valid phone number with country code (e.g., +1234567890)",
                            },
                          })}
                        />
                        {errors.from_number && (
                          <p className="text-sm text-red-500">
                            {errors.from_number.message}
                          </p>
                        )}
                      </div>
                    </>
                  )}

                  {/* Vonage-specific fields */}
                  {selectedProvider === "vonage" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="application_id">Application ID</Label>
                        <Input
                          id="application_id"
                          placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                          {...register("application_id", {
                            required: selectedProvider === "vonage" ? "Application ID is required" : false,
                          })}
                        />
                        {errors.application_id && (
                          <p className="text-sm text-red-500">
                            {errors.application_id.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="private_key">Private Key</Label>
                        <textarea
                          id="private_key"
                          className="w-full min-h-[100px] px-3 py-2 text-sm border rounded-md"
                          placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
                          {...register("private_key", {
                            required: selectedProvider === "vonage" && !hasExistingConfig
                              ? "Private key is required"
                              : false,
                          })}
                        />
                        {errors.private_key && (
                          <p className="text-sm text-red-500">
                            {errors.private_key.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="api_key">API Key (Optional)</Label>
                        <Input
                          id="api_key"
                          placeholder="Optional - for some operations"
                          {...register("api_key")}
                        />
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="api_secret">API Secret (Optional)</Label>
                        <Input
                          id="api_secret"
                          type="password"
                          placeholder="Optional - for webhook verification"
                          {...register("api_secret")}
                        />
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="from_number">From Phone Number</Label>
                        <Input
                          id="from_number"
                          autoComplete="tel"
                          placeholder="14155551234 (no + prefix for Vonage)"
                          {...register("from_number", {
                            required: "Phone number is required",
                            pattern: {
                              value: /^[1-9]\d{1,14}$/,
                              message:
                                "Enter a valid phone number without + prefix (e.g., 14155551234)",
                            },
                          })}
                        />
                        {errors.from_number && (
                          <p className="text-sm text-red-500">
                            {errors.from_number.message}
                          </p>
                        )}
                      </div>
                    </>
                  )}

                  {/* Vobiz-specific fields */}
                  {selectedProvider === "vobiz" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="auth_id">Auth ID</Label>
                        <Input
                          id="auth_id"
                          placeholder="MA_SYQRLN1K"
                          {...register("auth_id", {
                            required: selectedProvider === "vobiz" ? "Auth ID is required" : false,
                          })}
                        />
                        {errors.auth_id && (
                          <p className="text-sm text-red-500">
                            {errors.auth_id.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="vobiz_auth_token">Auth Token</Label>
                        <Input
                          id="vobiz_auth_token"
                          type="password"
                          autoComplete="current-password"
                          placeholder={
                            hasExistingConfig
                              ? "Leave masked to keep existing"
                              : "Enter your auth token"
                          }
                          {...register("vobiz_auth_token", {
                            required: selectedProvider === "vobiz" && !hasExistingConfig
                              ? "Auth token is required"
                              : false,
                          })}
                        />
                        {errors.vobiz_auth_token && (
                          <p className="text-sm text-red-500">
                            {errors.vobiz_auth_token.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="from_number">From Phone Number</Label>
                        <Input
                          id="from_number"
                          autoComplete="tel"
                          placeholder="14155551234 (no + prefix for Vobiz)"
                          {...register("from_number", {
                            required: "Phone number is required",
                            pattern: {
                              value: /^[1-9]\d{1,14}$/,
                              message:
                                "Enter a valid phone number without + prefix (e.g., 14155551234)",
                            },
                          })}
                        />
                        {errors.from_number && (
                          <p className="text-sm text-red-500">
                            {errors.from_number.message}
                          </p>
                        )}
                      </div>
                    </>
                  )}

                  {/* Cloudonix-specific fields */}
                  {selectedProvider === "cloudonix" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="bearer_token">Domain API Token (eg. XI-....)</Label>
                        <Input
                          id="bearer_token"
                          type="password"
                          autoComplete="current-password"
                          placeholder={
                            hasExistingConfig
                              ? "Leave masked to keep existing"
                              : "Enter your bearer token"
                          }
                          {...register("bearer_token", {
                            required:
                              selectedProvider === "cloudonix" && !hasExistingConfig
                                ? "Domain API token is required"
                                : false,
                          })}
                        />
                        {errors.bearer_token && (
                          <p className="text-sm text-red-500">
                            {errors.bearer_token.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="domain_id">Domain Name or UUID</Label>
                        <Input
                          id="domain_id"
                          placeholder="your-domain-id"
                          {...register("domain_id", {
                            required:
                              selectedProvider === "cloudonix"
                                ? "Domain Name or UUID is required"
                                : false,
                          })}
                        />
                        {errors.domain_id && (
                          <p className="text-sm text-red-500">
                            {errors.domain_id.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="from_number">
                          From Phone Number (Optional)
                        </Label>
                        <Input
                          id="from_number"
                          autoComplete="tel"
                          placeholder="+1234567890"
                          {...register("from_number", {
                            pattern: {
                              value: /^\+?[1-9]\d{1,14}$/,
                              message:
                                "Enter a valid phone number (e.g., +1234567890)",
                            },
                          })}
                        />
                        {errors.from_number && (
                          <p className="text-sm text-red-500">
                            {errors.from_number.message}
                          </p>
                        )}
                        <p className="text-xs text-gray-500">
                          Phone numbers can be fetched from Cloudonix DNIDs if not
                          specified
                        </p>
                      </div>
                    </>
                  )}

                  <div className="pt-4">
                    <Button
                      type="submit"
                      className="w-full"
                      disabled={isLoading}
                    >
                      {isLoading ? "Saving..." : "Save Configuration"}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </form>
          </div>

        </div>
      </div>
    </div>
  );
}
