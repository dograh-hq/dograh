"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { getTelephonyConfigurationApiV1OrganizationsTelephonyConfigGet, saveTelephonyConfigurationApiV1OrganizationsTelephonyConfigPost } from "@/client/sdk.gen";
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
  account_sid: string;
  auth_token: string;
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

        if (!response.error && response.data?.twilio) {
          setHasExistingConfig(true);
          // Masked values like "****************def0" from backend
          setValue("account_sid", response.data.twilio.account_sid);
          setValue("auth_token", response.data.twilio.auth_token);
          if (response.data.twilio.from_numbers?.length > 0) {
            setValue("from_number", response.data.twilio.from_numbers[0]);
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
      const response = await saveTelephonyConfigurationApiV1OrganizationsTelephonyConfigPost({
        headers: { Authorization: `Bearer ${accessToken}` },
        body: {
          provider: data.provider,
          account_sid: data.account_sid,
          auth_token: data.auth_token,
          from_numbers: [data.from_number],
        },
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
                <CardTitle>Setup Guide</CardTitle>
                <CardDescription>
                  Watch this video to learn how to setup telephony
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="aspect-video">
                  <iframe
                    style={{ border: 0 }}
                    width="100%"
                    height="100%"
                    src="https://www.tella.tv/video/cmgbvzkrt00jk0clacu16blm3/embed?b=0&title=1&a=1&loop=0&t=0&muted=0&wt=0"
                    allowFullScreen
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                  />
                </div>
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
                      </SelectContent>
                    </Select>
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
