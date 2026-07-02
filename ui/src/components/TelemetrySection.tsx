"use client";

import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  deleteLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsDelete,
  getLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsGet,
  saveLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsPost,
} from "@/client/sdk.gen";
import type { LangfuseCredentialsResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";

export function TelemetrySection() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const { user, loading: authLoading } = useAuth();
  const [credentials, setCredentials] = useState<LangfuseCredentialsResponse>({
    host: "",
    public_key: "",
    secret_key: "",
    configured: false,
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    fetchCredentials();
  }, [authLoading, user]);

  async function fetchCredentials() {
    try {
      const { data } = await getLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsGet();
      if (data) {
        setCredentials(data);
      }
    } catch {
      // No credentials configured yet — that's fine
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const { error } = await saveLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsPost({
        body: {
          host: credentials.host ?? "",
          public_key: credentials.public_key ?? "",
          secret_key: credentials.secret_key ?? "",
        },
      });
      if (error) {
        throw new Error("Failed to save");
      }
      toast.success(t("telemetry.saveSuccess"));
      await fetchCredentials();
    } catch {
      toast.error(t("telemetry.saveError"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await deleteLangfuseCredentialsApiV1OrganizationsLangfuseCredentialsDelete();
      setCredentials({ host: "", public_key: "", secret_key: "", configured: false });
      toast.success(t("telemetry.removeSuccess"));
    } catch {
      toast.error(t("telemetry.removeError"));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">{tc("loading")}</p>;
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      <p className="text-sm text-muted-foreground">{t("telemetry.description")}</p>
      <div className="space-y-2">
        <Label htmlFor="langfuse-host">{t("telemetry.host")}</Label>
        <Input
          id="langfuse-host"
          placeholder={t("telemetry.hostPlaceholder")}
          value={credentials.host}
          onChange={(e) => setCredentials({ ...credentials, host: e.target.value })}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="langfuse-public-key">{t("telemetry.publicKey")}</Label>
        <Input
          id="langfuse-public-key"
          placeholder={t("telemetry.publicKeyPlaceholder")}
          value={credentials.public_key}
          onChange={(e) => setCredentials({ ...credentials, public_key: e.target.value })}
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="langfuse-secret-key">{t("telemetry.secretKey")}</Label>
        <Input
          id="langfuse-secret-key"
          type="password"
          placeholder={t("telemetry.secretKeyPlaceholder")}
          value={credentials.secret_key}
          onChange={(e) => setCredentials({ ...credentials, secret_key: e.target.value })}
          required
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={saving}>
          {saving ? t("telemetry.saving") : t("telemetry.save")}
        </Button>
        {credentials.configured && (
          <Button type="button" variant="destructive" disabled={saving} onClick={handleDelete}>
            {t("telemetry.remove")}
          </Button>
        )}
      </div>
    </form>
  );
}
