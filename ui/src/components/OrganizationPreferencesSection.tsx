"use client";

import { Save } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import TimezoneSelect, { type ITimezoneOption } from "react-timezone-select";
import { toast } from "sonner";

import {
  getPreferencesApiV1OrganizationsPreferencesGet,
  savePreferencesApiV1OrganizationsPreferencesPut,
} from "@/client/sdk.gen";
import type { OrganizationPreferences } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useUserConfig } from "@/context/UserConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const emptyPreferences: OrganizationPreferences = {
  test_phone_number: "",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  external_pbx_integrations_enabled: false,
};

const timezoneSelectStyles = {
  control: (base: Record<string, unknown>, state: { isFocused: boolean }) => ({
    ...base,
    minHeight: "36px",
    fontSize: "14px",
    backgroundColor: "var(--background)",
    borderColor: state.isFocused ? "var(--ring)" : "var(--border)",
    boxShadow: state.isFocused
      ? "0 0 0 2px color-mix(in srgb, var(--ring) 20%, transparent)"
      : "none",
    "&:hover": { borderColor: "var(--border)" },
  }),
  menu: (base: Record<string, unknown>) => ({
    ...base,
    zIndex: 9999,
    backgroundColor: "var(--popover)",
    border: "1px solid var(--border)",
    boxShadow:
      "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
  }),
  menuList: (base: Record<string, unknown>) => ({
    ...base,
    backgroundColor: "var(--popover)",
    padding: 0,
  }),
  option: (
    base: Record<string, unknown>,
    state: { isFocused: boolean; isSelected: boolean },
  ) => ({
    ...base,
    backgroundColor: state.isSelected
      ? "var(--accent)"
      : state.isFocused
        ? "var(--accent)"
        : "var(--popover)",
    color: "var(--foreground)",
    cursor: "pointer",
    "&:active": { backgroundColor: "var(--accent)" },
  }),
  singleValue: (base: Record<string, unknown>) => ({
    ...base,
    color: "var(--foreground)",
  }),
  input: (base: Record<string, unknown>) => ({
    ...base,
    color: "var(--foreground)",
  }),
  placeholder: (base: Record<string, unknown>) => ({
    ...base,
    color: "var(--muted-foreground)",
  }),
  indicatorSeparator: (base: Record<string, unknown>) => ({
    ...base,
    backgroundColor: "var(--border)",
  }),
  dropdownIndicator: (base: Record<string, unknown>) => ({
    ...base,
    color: "var(--muted-foreground)",
    "&:hover": { color: "var(--foreground)" },
  }),
};

function getTimezoneValue(tz: ITimezoneOption | string): string {
  return typeof tz === "string" ? tz : tz.value;
}

export function OrganizationPreferencesSection() {
  const { user, loading: authLoading } = useAuth();
  const { refreshConfig } = useUserConfig();
  const timezoneSelectId = useId();
  const hasFetched = useRef(false);

  const [preferences, setPreferences] =
    useState<OrganizationPreferences>(emptyPreferences);
  const [timezone, setTimezone] = useState<ITimezoneOption | string>(
    emptyPreferences.timezone || "UTC",
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    void fetchPreferences();
  }, [authLoading, user]);

  async function fetchPreferences() {
    setLoading(true);
    try {
      const result =
        await getPreferencesApiV1OrganizationsPreferencesGet();

      if (result.error) {
        toast.error(
          detailFromError(
            result.error,
            "Failed to load organization preferences",
          ),
        );
        return;
      }

      const nextPreferences = result.data || emptyPreferences;
      setPreferences({
        test_phone_number: nextPreferences.test_phone_number || "",
        timezone: nextPreferences.timezone || emptyPreferences.timezone,
        external_pbx_integrations_enabled:
          nextPreferences.external_pbx_integrations_enabled ?? false,
      });
      setTimezone(
        nextPreferences.timezone || emptyPreferences.timezone || "UTC",
      );
    } catch {
      toast.error("Failed to load organization preferences");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const result =
        await savePreferencesApiV1OrganizationsPreferencesPut(
          {
            body: {
              test_phone_number: preferences.test_phone_number || null,
              timezone: getTimezoneValue(timezone),
              external_pbx_integrations_enabled:
                preferences.external_pbx_integrations_enabled ?? false,
            },
          },
        );

      if (result.error) {
        toast.error(detailFromError(result.error, "Failed to save preferences"));
        return;
      }
      if (!result.data) {
        toast.error("Failed to save preferences");
        return;
      }

      setPreferences({
        test_phone_number: result.data.test_phone_number || "",
        timezone: result.data.timezone || emptyPreferences.timezone,
        external_pbx_integrations_enabled:
          result.data.external_pbx_integrations_enabled ?? false,
      });
      setTimezone(result.data.timezone || emptyPreferences.timezone || "UTC");
      await refreshConfig();
      toast.success("Preferences saved");
    } catch {
      toast.error("Failed to save preferences");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Set organization-wide defaults used by testing and scheduling flows.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="settings-test-phone-number">Test Phone Number</Label>
          <Input
            id="settings-test-phone-number"
            value={preferences.test_phone_number || ""}
            onChange={(event) =>
              setPreferences({
                ...preferences,
                test_phone_number: event.target.value,
              })
            }
            placeholder="+15551234567"
          />
        </div>
        <div className="space-y-2">
          <Label>Timezone</Label>
          <TimezoneSelect
            instanceId={timezoneSelectId}
            value={timezone}
            onChange={setTimezone}
            styles={timezoneSelectStyles}
          />
        </div>
      </div>
      <div className="flex items-start justify-between gap-4 rounded-lg border p-4">
        <div className="space-y-1">
          <Label htmlFor="settings-external-pbx-integrations">
            External PBX integrations
          </Label>
          <p className="text-xs text-muted-foreground">
            Show and enable advanced external-PBX configuration for Asterisk,
            transfer tools, and workflows. Existing configuration is preserved
            when this is disabled.
          </p>
        </div>
        <Switch
          id="settings-external-pbx-integrations"
          checked={preferences.external_pbx_integrations_enabled ?? false}
          onCheckedChange={(checked) =>
            setPreferences({
              ...preferences,
              external_pbx_integrations_enabled: checked,
            })
          }
        />
      </div>
      <Button type="submit" disabled={saving}>
        <Save className="mr-2 h-4 w-4" />
        {saving ? "Saving..." : "Save"}
      </Button>
    </form>
  );
}
