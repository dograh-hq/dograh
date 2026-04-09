"use client";

import { ExternalLink, Plus, Trash2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet, saveTelephonyConfigurationApiV1OrganizationsTelephonyConfigPost } from "@/client/sdk.gen";
import type {
  AriConfigurationRequest,
  AriConfigurationResponse,
  CloudonixConfigurationRequest,
  CloudonixConfigurationResponse,
  TelephonyConfigurationResponse,
  TelnyxConfigurationRequest,
  TelnyxConfigurationResponse,
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
  // Telnyx fields
  telnyx_api_key?: string;
  connection_id?: string;
  // Cloudonix fields
  bearer_token?: string;
  domain_id?: string;
  // ARI fields
  ari_endpoint?: string;
  app_name?: string;
  app_password?: string;
  ws_client_name?: string;
  inbound_workflow_id?: number;
  // Common field - multiple phone numbers
  from_numbers: string[];
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
      from_numbers: [""],
    },
  });

  const selectedProvider = watch("provider");
  const fromNumbers = watch("from_numbers") || [""];

  const addPhoneNumber = () => {
    setValue("from_numbers", [...fromNumbers, ""]);
  };

  const removePhoneNumber = (index: number) => {
    if (fromNumbers.length > 1) {
      setValue("from_numbers", fromNumbers.filter((_, i) => i !== index));
    }
  };

  const updatePhoneNumber = (index: number, value: string) => {
    const newNumbers = [...fromNumbers];
    newNumbers[index] = value;
    setValue("from_numbers", newNumbers);
  };

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
            setValue("from_numbers", response.data.twilio.from_numbers?.length > 0 ? response.data.twilio.from_numbers : [""]);
          } else if (response.data?.vonage) {
            setHasExistingConfig(true);
            setValue("provider", "vonage");
            setValue("application_id", response.data.vonage.application_id);
            setValue("private_key", response.data.vonage.private_key);
            setValue("api_key", response.data.vonage.api_key || "");
            setValue("api_secret", response.data.vonage.api_secret || "");
            setValue("from_numbers", response.data.vonage.from_numbers?.length > 0 ? response.data.vonage.from_numbers : [""]);
          } else if (response.data?.vobiz) {
            setHasExistingConfig(true);
            setValue("provider", "vobiz");
            setValue("auth_id", response.data.vobiz.auth_id);
            setValue("vobiz_auth_token", response.data.vobiz.auth_token);
            setValue("from_numbers", response.data.vobiz.from_numbers?.length > 0 ? response.data.vobiz.from_numbers : [""]);
          } else if ((response.data as TelephonyConfigurationResponse)?.cloudonix) {
            const cloudonixConfig = (response.data as TelephonyConfigurationResponse).cloudonix as CloudonixConfigurationResponse;
            setHasExistingConfig(true);
            setValue("provider", "cloudonix");
            setValue("bearer_token", cloudonixConfig.bearer_token);
            setValue("domain_id", cloudonixConfig.domain_id);
            setValue("from_numbers", cloudonixConfig.from_numbers?.length > 0 ? cloudonixConfig.from_numbers : [""]);
          } else if ((response.data as TelephonyConfigurationResponse)?.ari) {
            const ariConfig = (response.data as TelephonyConfigurationResponse).ari as AriConfigurationResponse;
            setHasExistingConfig(true);
            setValue("provider", "ari");
            setValue("ari_endpoint", ariConfig.ari_endpoint);
            setValue("app_name", ariConfig.app_name);
            setValue("app_password", ariConfig.app_password);
            setValue("ws_client_name", ariConfig.ws_client_name);
            setValue(
              "inbound_workflow_id",
              typeof ariConfig.inbound_workflow_id === "number" ? ariConfig.inbound_workflow_id : undefined
            );
            setValue("from_numbers", ariConfig.from_numbers?.length > 0 ? ariConfig.from_numbers : [""]);
          } else if ((response.data as TelephonyConfigurationResponse)?.telnyx) {
            const telnyxConfig = (response.data as TelephonyConfigurationResponse).telnyx as TelnyxConfigurationResponse;
            setHasExistingConfig(true);
            setValue("provider", "telnyx");
            setValue("telnyx_api_key", telnyxConfig.api_key);
            setValue("connection_id", telnyxConfig.connection_id);
            setValue("from_numbers", telnyxConfig.from_numbers?.length > 0 ? telnyxConfig.from_numbers : [""]);
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
        | CloudonixConfigurationRequest
        | AriConfigurationRequest
        | TelnyxConfigurationRequest;

      const filteredNumbers = data.from_numbers.filter(n => n.trim() !== "");

      // Validate phone numbers are provided (except for Cloudonix/ARI where optional)
      if (data.provider !== "cloudonix" && data.provider !== "ari" && filteredNumbers.length === 0) {
        toast.error("At least one phone number is required");
        setIsLoading(false);
        return;
      }

      // Validate phone number format based on provider
      const twilioPattern = /^\+[1-9]\d{1,14}$/;
      const vonageVobizPattern = /^[1-9]\d{1,14}$/;
      const cloudonixPattern = /^\+?[1-9]\d{1,14}$/;

      let pattern: RegExp;
      let formatMessage: string;
      if (data.provider === "twilio" || data.provider === "telnyx") {
        pattern = twilioPattern;
        formatMessage = "with + prefix (e.g., +1234567890)";
      } else if (data.provider === "cloudonix") {
        pattern = cloudonixPattern;
        formatMessage = "(e.g., +1234567890)";
      } else if (data.provider === "ari") {
        // ARI uses SIP extensions - skip phone number validation
        pattern = /^.+$/;
        formatMessage = "(SIP extension or number)";
      } else {
        pattern = vonageVobizPattern;
        formatMessage = "without + prefix (e.g., 14155551234)";
      }

      const invalidNumbers = filteredNumbers.filter(n => !pattern.test(n));
      if (invalidNumbers.length > 0) {
        toast.error(`Invalid phone number format. Please enter numbers ${formatMessage}`);
        setIsLoading(false);
        return;
      }

      if (data.provider === "twilio") {
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          account_sid: data.account_sid,
          auth_token: data.auth_token,
        } as TwilioConfigurationRequest;
      } else if (data.provider === "vonage") {
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          application_id: data.application_id,
          private_key: data.private_key,
          api_key: data.api_key || undefined,
          api_secret: data.api_secret || undefined,
        } as VonageConfigurationRequest;
      } else if (data.provider === "vobiz") {
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          auth_id: data.auth_id,
          auth_token: data.vobiz_auth_token,
        } as VobizConfigurationRequest;
      } else if (data.provider === "telnyx") {
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          api_key: data.telnyx_api_key!,
          connection_id: data.connection_id!,
        } as TelnyxConfigurationRequest;
      } else if (data.provider === "cloudonix") {
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          bearer_token: data.bearer_token!,
          domain_id: data.domain_id!,
        } as CloudonixConfigurationRequest;
      } else {
        // ARI
        requestBody = {
          provider: data.provider,
          from_numbers: filteredNumbers,
          ari_endpoint: data.ari_endpoint!,
          app_name: data.app_name!,
          app_password: data.app_password!,
          ws_client_name: data.ws_client_name || "",
          inbound_workflow_id: data.inbound_workflow_id || undefined,
        } as AriConfigurationRequest;
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
    <div className="min-h-screen bg-background">
      <div className="container mx-auto px-4 py-8">
        <h1 className="text-3xl font-bold mb-2">Configure Telephony</h1>
        <p className="text-muted-foreground mb-6">
          Set up your telephony provider to make phone calls.{" "}
          <a href="https://docs.dograh.com/integrations/telephony/overview" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
            Learn more <ExternalLink className="h-3 w-3" />
          </a>
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
                    : selectedProvider === "telnyx"
                    ? "Telnyx"
                    : selectedProvider === "ari"
                    ? "Asterisk ARI"
                    : "Cloudonix"}{" "}
                  Setup Guide
                </CardTitle>
                <CardDescription>
                  {selectedProvider === "telnyx" ? (
                    <>
                      Telnyx is a cloud communications platform providing programmable voice, messaging,
                      and networking services. Use the Call Control API to build voice applications
                      with real-time WebSocket audio streaming.
                    </>
                  ) : selectedProvider === "ari" ? (
                    <>
                      Connect Dograh to your Asterisk PBX using the Asterisk REST Interface (ARI).
                      ARI provides a WebSocket-based event model for controlling calls via Stasis applications.
                    </>
                  ) : selectedProvider === "cloudonix" ? (
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
                {selectedProvider === "telnyx" ? (
                  <div className="space-y-4 text-sm">
                    <div>
                      <h4 className="font-semibold mb-2">Getting Started with Telnyx:</h4>
                      <ol className="list-decimal list-inside space-y-1 text-muted-foreground">
                        <li>Sign up at <a href="https://telnyx.com" target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">telnyx.com</a> and create an API Key in the Mission Control Portal</li>
                        <li>Create a Call Control Application under Voice &gt; Programmable Voice</li>
                        <li>Note the Connection ID (Application ID) from your Call Control App</li>
                        <li>Purchase a phone number and assign it to your Call Control Application</li>
                        <li>Enter your API Key, Connection ID, and phone numbers below</li>
                      </ol>
                    </div>
                    <div className="bg-muted border border-border rounded p-3">
                      <p className="text-sm">
                        <strong>Note:</strong> Telnyx uses the Call Control API with WebSocket-based
                        bidirectional audio streaming. Phone numbers must be in E.164 format (e.g., +1234567890).
                      </p>
                    </div>
                  </div>
                ) : selectedProvider === "ari" ? (
                  <div className="space-y-4 text-sm">
                    <div>
                      <h4 className="font-semibold mb-2">Getting Started with Asterisk ARI:</h4>
                      <ol className="list-decimal list-inside space-y-1 text-muted-foreground">
                        <li>Enable the ARI module in your Asterisk configuration (ari.conf)</li>
                        <li>Create an ARI user with a password in ari.conf</li>
                        <li>Create a Stasis application in your dialplan (extensions.conf)</li>
                        <li>Ensure the ARI HTTP endpoint is accessible from Dograh</li>
                        <li>Enter your ARI endpoint URL, app name, and password below</li>
                      </ol>
                    </div>
                    <div className="bg-muted border border-border rounded p-3">
                      <p className="text-sm">
                        <strong>Note:</strong> ARI uses WebSocket connections for real-time
                        event listening. The ARI manager process will automatically connect
                        to your Asterisk instance once configured.
                      </p>
                    </div>
                  </div>
                ) : selectedProvider === "twilio" || selectedProvider === "vonage" ? (
                  <div className="aspect-video">
                    <iframe
                      style={{ border: 0 }}
                      width="100%"
                      height="100%"
                      src={
                        selectedProvider === "twilio"
                          ? "https://www.youtube.com/embed/jlPD4CSJHHI"
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
                      <ol className="list-decimal list-inside space-y-1 text-muted-foreground">
                        <li>Sign up for a Vobiz account</li>
                        <li>Get your Auth ID from the Vobiz dashboard</li>
                        <li>Generate an Auth Token</li>
                        <li>Configure phone numbers in your Vobiz account</li>
                        <li>Enter your credentials below</li>
                      </ol>
                    </div>
                    <div className="bg-muted border border-border rounded p-3">
                      <p className="text-sm">
                        <strong>Note:</strong> Vobiz provides cloud-based telephony services
                        with global reach and competitive pricing.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4 text-sm">
                    <div>
                      <h4 className="font-semibold mb-2">Getting Started with Cloudonix:</h4>
                      <ol className="list-decimal list-inside space-y-1 text-muted-foreground">
                        <li>Sign up for a Cloudonix account at https://cloudonix.com</li>
                        <li>Create an <i>API token</i> for your Cloudonix domain</li>
                        <li>Configure your Cloudoinx <i>API Token</i> and <i>Cloudonix Domain Name</i> in Dograh</li>
                        <li>Configure an optional outbound phone number for your Dograh agent</li>
                      </ol>
                    </div>
                    <div className="bg-muted border border-border rounded p-3">
                      <p className="text-sm">
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
                        <SelectItem value="telnyx">Telnyx</SelectItem>
                        <SelectItem value="cloudonix">Cloudonix</SelectItem>
                        <SelectItem value="ari">Asterisk (ARI)</SelectItem>
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
                        <Label>CLI Phone Numbers</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              autoComplete="tel"
                              placeholder="+1234567890"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Phone Number
                        </Button>
                        {fromNumbers.some(n => n.trim() !== "" && !/^\+[1-9]\d{1,14}$/.test(n)) && (
                          <p className="text-sm text-red-500">
                            Enter valid phone numbers with country code (e.g., +1234567890)
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
                          className="w-full min-h-[100px] px-3 py-2 text-sm border border-input bg-background rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
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
                        <Label>CLI Phone Numbers</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              autoComplete="tel"
                              placeholder="14155551234 (no + prefix for Vonage)"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Phone Number
                        </Button>
                        {fromNumbers.some(n => n.trim() !== "" && !/^[1-9]\d{1,14}$/.test(n)) && (
                          <p className="text-sm text-red-500">
                            Enter valid phone numbers without + prefix (e.g., 14155551234)
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
                        <Label>CLI Phone Numbers</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              autoComplete="tel"
                              placeholder="14155551234 (no + prefix for Vobiz)"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Phone Number
                        </Button>
                        {fromNumbers.some(n => n.trim() !== "" && !/^[1-9]\d{1,14}$/.test(n)) && (
                          <p className="text-sm text-red-500">
                            Enter valid phone numbers without + prefix (e.g., 14155551234)
                          </p>
                        )}
                      </div>
                    </>
                  )}

                  {/* Telnyx-specific fields */}
                  {selectedProvider === "telnyx" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="telnyx_api_key">API Key</Label>
                        <Input
                          id="telnyx_api_key"
                          type="password"
                          autoComplete="current-password"
                          placeholder={
                            hasExistingConfig
                              ? "Leave masked to keep existing"
                              : "Enter your Telnyx API key"
                          }
                          {...register("telnyx_api_key", {
                            required: selectedProvider === "telnyx" && !hasExistingConfig
                              ? "API Key is required"
                              : false,
                          })}
                        />
                        {errors.telnyx_api_key && (
                          <p className="text-sm text-red-500">
                            {errors.telnyx_api_key.message}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          Found in the Telnyx Mission Control Portal under Auth &gt; API Keys
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="connection_id">Connection ID (Application ID)</Label>
                        <Input
                          id="connection_id"
                          placeholder="1234567890"
                          {...register("connection_id", {
                            required: selectedProvider === "telnyx"
                              ? "Connection ID is required"
                              : false,
                          })}
                        />
                        {errors.connection_id && (
                          <p className="text-sm text-red-500">
                            {errors.connection_id.message}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          The ID of your Call Control Application in Telnyx Mission Control
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label>CLI Phone Numbers</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              autoComplete="tel"
                              placeholder="+1234567890"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Phone Number
                        </Button>
                        {fromNumbers.some(n => n.trim() !== "" && !/^\+[1-9]\d{1,14}$/.test(n)) && (
                          <p className="text-sm text-red-500">
                            Enter valid phone numbers with country code (e.g., +1234567890)
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
                        <Label>CLI Phone Numbers (Optional)</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              autoComplete="tel"
                              placeholder="+1234567890"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Phone Number
                        </Button>
                        {fromNumbers.some(n => n.trim() !== "" && !/^\+?[1-9]\d{1,14}$/.test(n)) && (
                          <p className="text-sm text-red-500">
                            Enter valid phone numbers (e.g., +1234567890)
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          Phone numbers can be fetched from Cloudonix DNIDs if not
                          specified
                        </p>
                      </div>
                    </>
                  )}

                  {/* ARI-specific fields */}
                  {selectedProvider === "ari" && (
                    <>
                      <div className="space-y-2">
                        <Label htmlFor="ari_endpoint">ARI Endpoint URL</Label>
                        <Input
                          id="ari_endpoint"
                          placeholder="http://asterisk.example.com:8088"
                          {...register("ari_endpoint", {
                            required:
                              selectedProvider === "ari"
                                ? "ARI endpoint URL is required"
                                : false,
                          })}
                        />
                        {errors.ari_endpoint && (
                          <p className="text-sm text-red-500">
                            {errors.ari_endpoint.message}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          The HTTP base URL for your Asterisk ARI (e.g., http://host:8088)
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="app_name">Stasis App Name</Label>
                        <Input
                          id="app_name"
                          placeholder="dograh"
                          {...register("app_name", {
                            required:
                              selectedProvider === "ari"
                                ? "Stasis app name is required"
                                : false,
                          })}
                        />
                        {errors.app_name && (
                          <p className="text-sm text-red-500">
                            {errors.app_name.message}
                          </p>
                        )}
                        <p className="text-xs text-muted-foreground">
                          The ARI username and Stasis application name configured in ari.conf
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="app_password">App Password</Label>
                        <Input
                          id="app_password"
                          type="password"
                          autoComplete="current-password"
                          placeholder={
                            hasExistingConfig
                              ? "Leave masked to keep existing"
                              : "Enter your ARI password"
                          }
                          {...register("app_password", {
                            required:
                              selectedProvider === "ari" && !hasExistingConfig
                                ? "App password is required"
                                : false,
                          })}
                        />
                        {errors.app_password && (
                          <p className="text-sm text-red-500">
                            {errors.app_password.message}
                          </p>
                        )}
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="ws_client_name">WebSocket Client Name</Label>
                        <Input
                          id="ws_client_name"
                          placeholder="dograh_staging"
                          {...register("ws_client_name")}
                        />
                        <p className="text-xs text-muted-foreground">
                          Connection name from Asterisk&apos;s websocket_client.conf for external media streaming
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="inbound_workflow_id">Inbound Workflow ID (Optional)</Label>
                        <Input
                          id="inbound_workflow_id"
                          type="number"
                          placeholder="e.g. 42"
                          {...register("inbound_workflow_id", { valueAsNumber: true })}
                        />
                        <p className="text-xs text-muted-foreground">
                          Workflow to activate for inbound calls received via ARI
                        </p>
                      </div>

                      <div className="space-y-2">
                        <Label>SIP Extensions / Numbers (Optional)</Label>
                        {fromNumbers.map((number, index) => (
                          <div key={index} className="flex gap-2">
                            <Input
                              placeholder="PJSIP/6001 or 6001"
                              value={number}
                              onChange={(e) => updatePhoneNumber(index, e.target.value)}
                            />
                            {fromNumbers.length > 1 && (
                              <Button
                                type="button"
                                variant="outline"
                                size="icon"
                                onClick={() => removePhoneNumber(index)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            )}
                          </div>
                        ))}
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={addPhoneNumber}
                        >
                          <Plus className="h-4 w-4 mr-2" />
                          Add Extension
                        </Button>
                        <p className="text-xs text-muted-foreground">
                          SIP extensions or trunk numbers for outbound calls
                        </p>
                      </div>
                    </>
                  )}

                  <div className="pt-4 space-y-3">
                    <Button
                      type="submit"
                      className="w-full"
                      disabled={isLoading}
                    >
                      {isLoading ? "Saving..." : "Save Configuration"}
                    </Button>
                    <div className="text-center">
                      <p className="text-xs text-muted-foreground">
                        Configure inbound calling?{" "}
                        <a
                          href="https://docs.dograh.com/integrations/telephony/inbound"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 dark:text-blue-400 hover:underline"
                        >
                          View documentation
                        </a>
                      </p>
                    </div>
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
