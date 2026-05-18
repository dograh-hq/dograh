"use client";

import { Copy, ExternalLink } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost,
  getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet,
  updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut,
} from "@/client/sdk.gen";
import type {
  TelephonyConfigurationCreateRequest,
  TelephonyConfigurationDetail,
  TelephonyProviderMetadata,
} from "@/client/types.gen";

type TelephonyConfigPayload = TelephonyConfigurationCreateRequest["config"];
import { Button } from "@/components/ui/button";
import {
  Dialog,
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";

interface ConfigFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // When provided, the dialog is in edit mode.
  existing?: TelephonyConfigurationDetail | null;
  onSaved: () => void;
}

type FieldValues = Record<string, string | number | undefined>;

export function ConfigFormDialog({
  open,
  onOpenChange,
  existing,
  onSaved,
}: ConfigFormDialogProps) {
  const { user, getAccessToken } = useAuth();
  const [providers, setProviders] = useState<TelephonyProviderMetadata[]>([]);
  const [providerName, setProviderName] = useState<string>("");
  const [name, setName] = useState<string>("");
  const [isDefault, setIsDefault] = useState<boolean>(false);
  const [values, setValues] = useState<FieldValues>({});
  const [submitting, setSubmitting] = useState<boolean>(false);

  const isEdit = !!existing;
  const lockedProvider = isEdit;

  const currentProvider = useMemo(
    () => providers.find((p) => p.provider === providerName),
    [providers, providerName],
  );

  // Fetch provider metadata once when the dialog opens.
  useEffect(() => {
    if (!open || !user) return;
    let cancelled = false;
    (async () => {
      const token = await getAccessToken();
      const res = await getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet(
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (cancelled) return;
      const list = res.data?.providers ?? [];
      setProviders(list);
      if (existing) {
        setProviderName(existing.provider);
        setName(existing.name);
        setIsDefault(existing.is_default_outbound);
        setValues((existing.credentials ?? {}) as FieldValues);
      } else if (list.length > 0 && !providerName) {
        setProviderName(list[0].provider);
        setValues({});
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, existing, user, getAccessToken]);

  // When provider changes during create, clear field values.
  useEffect(() => {
    if (!isEdit) setValues({});
  }, [providerName, isEdit]);

  const updateField = (fieldName: string, value: string | number) => {
    setValues((prev) => ({ ...prev, [fieldName]: value }));
  };

  const handleSubmit = async () => {
    if (!currentProvider) return;
    if (!isEdit && !name.trim()) {
      toast.error("Name is required");
      return;
    }

    setSubmitting(true);
    try {
      const token = await getAccessToken();

      // Build the provider-discriminated config payload from collected values.
      const configPayload = {
        provider: providerName,
        ...values,
      } as unknown as TelephonyConfigPayload;

      if (isEdit && existing) {
        const res = await updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut(
          {
            headers: { Authorization: `Bearer ${token}` },
            path: { config_id: existing.id },
            body: { name: name || undefined, config: configPayload },
          },
        );
        if (res.error) throw new Error(detailFromError(res.error));
        toast.success("Configuration updated");
      } else {
        const res = await createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost(
          {
            headers: { Authorization: `Bearer ${token}` },
            body: {
              name: name.trim(),
              is_default_outbound: isDefault,
              config: configPayload,
            },
          },
        );
        if (res.error) throw new Error(detailFromError(res.error));
        toast.success("Configuration created");
      }
      onOpenChange(false);
      onSaved();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit telephony configuration" : "Add telephony configuration"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update credentials for this configuration. Phone numbers are managed separately."
              : "Connect a telephony provider account. Phone numbers are added after the configuration is created."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {isEdit && existing && (
            <div className="space-y-1">
              <Label>Configuration ID</Label>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard
                    .writeText(String(existing.id))
                    .then(() => toast.success("Configuration ID copied"))
                    .catch(() => toast.error("Failed to copy ID"));
                }}
                title="Click to copy"
                className="group flex w-full items-center gap-2 rounded-md border bg-muted/20 p-2 text-left font-mono text-xs transition-colors hover:bg-muted/40"
              >
                <code className="flex-1 truncate">{existing.id}</code>
                <Copy className="h-3 w-3 shrink-0 text-muted-foreground group-hover:text-foreground" />
              </button>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="cfg-name">Name</Label>
            <Input
              id="cfg-name"
              placeholder="e.g. Twilio US prod"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="cfg-provider">Provider</Label>
            <Select
              value={providerName}
              onValueChange={setProviderName}
              disabled={lockedProvider || providers.length === 0}
            >
              <SelectTrigger id="cfg-provider">
                <SelectValue placeholder="Select a provider" />
              </SelectTrigger>
              <SelectContent>
                {providers.map((p) => (
                  <SelectItem key={p.provider} value={p.provider}>
                    {p.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {lockedProvider && (
              <p className="text-xs text-muted-foreground">
                Provider cannot be changed after creation.
              </p>
            )}
            {currentProvider?.docs_url && (
              <a
                href={currentProvider.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-blue-600 underline"
              >
                {currentProvider.display_name} docs <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>

          {!isEdit && (
            <div className="flex items-center justify-between rounded border p-3">
              <div>
                <Label className="text-sm">Set as default for outbound calls</Label>
                <p className="text-xs text-muted-foreground">
                  Used by test calls and campaigns when no specific config is selected.
                </p>
              </div>
              <Switch checked={isDefault} onCheckedChange={setIsDefault} />
            </div>
          )}

          {currentProvider && (
            <div className="space-y-3 border-t pt-3">
              {currentProvider.fields.map((field) => (
                <div className="space-y-1" key={field.name}>
                  <Label htmlFor={`cfg-field-${field.name}`}>
                    {field.label}
                    {!field.required && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        (optional)
                      </span>
                    )}
                  </Label>
                  <FieldInput
                    field={field}
                    value={values[field.name]}
                    onChange={(v) => updateField(field.name, v)}
                    isEdit={isEdit}
                  />
                  {field.description && (
                    <p className="text-xs text-muted-foreground">{field.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !currentProvider}>
            {submitting ? "Saving..." : isEdit ? "Save changes" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface FieldInputProps {
  field: TelephonyProviderMetadata["fields"][number];
  value: string | number | undefined;
  onChange: (v: string | number) => void;
  isEdit: boolean;
}

// Skip from_numbers in the metadata-driven form — phone numbers are managed
// via the dedicated phone-numbers endpoints and a different UI.
function FieldInput({ field, value, onChange, isEdit }: FieldInputProps) {
  if (field.name === "from_numbers") {
    return (
      <p className="text-xs text-muted-foreground">
        Phone numbers are managed separately on the configuration page.
      </p>
    );
  }

  const placeholder =
    field.placeholder ??
    (field.sensitive && isEdit ? "Leave masked to keep existing" : "");

  if (field.type === "textarea") {
    return (
      <Textarea
        id={`cfg-field-${field.name}`}
        placeholder={placeholder}
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value)}
        rows={6}
        className="field-sizing-fixed resize-y break-all font-mono text-xs"
      />
    );
  }
  if (field.type === "number") {
    return (
      <Input
        id={`cfg-field-${field.name}`}
        type="number"
        placeholder={placeholder}
        value={value as number | string | undefined ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
      />
    );
  }
  return (
    <Input
      id={`cfg-field-${field.name}`}
      type={field.type === "password" || field.sensitive ? "password" : "text"}
      placeholder={placeholder}
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      autoComplete={field.sensitive ? "current-password" : undefined}
    />
  );
}

// FastAPI error responses come back as { detail: string } or
// { detail: [{loc, msg, ...}] }. Surface a useful message either way.
function detailFromError(err: unknown): string {
  if (typeof err === "string") return err;
  const e = err as { detail?: unknown };
  if (typeof e?.detail === "string") return e.detail;
  if (Array.isArray(e?.detail) && e.detail.length > 0) {
    const first = e.detail[0] as { msg?: string };
    if (first?.msg) return first.msg;
  }
  return "Failed to save configuration";
}
