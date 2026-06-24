"use client";

import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";

// Mirrors api/schemas/crm_config.py::CRMConfig. After each qualifying call we upsert
// the contact (by phone) and log a call note with disposition/recording/transcript/
// sentiment. Calls go through the shared hey-api client (no SDK regen needed).
interface CRMConfig {
  enabled: boolean;
  provider: string;
  api_key: string;
  location_id: string;
  region_host: string;
  trigger_dispositions: string[];
  trigger_sentiments: string[];
  min_call_seconds: number;
}

const EMPTY: CRMConfig = {
  enabled: false,
  provider: "gohighlevel",
  api_key: "",
  location_id: "",
  region_host: "",
  trigger_dispositions: [],
  trigger_sentiments: [],
  min_call_seconds: 0,
};

const BASE = "/api/v1/organizations/crm-config";

export function CrmSection() {
  const { user, loading: authLoading } = useAuth();
  const [cfg, setCfg] = useState<CRMConfig>(EMPTY);
  const [exists, setExists] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testPhone, setTestPhone] = useState("");
  const [testing, setTesting] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    (async () => {
      try {
        const res = await client.get({ url: BASE });
        const data = res.data as { config: CRMConfig | null } | undefined;
        if (data?.config) {
          setExists(true);
          setCfg({ ...EMPTY, ...data.config });
        }
      } catch {
        // nothing configured yet
      } finally {
        setLoading(false);
      }
    })();
  }, [authLoading, user]);

  function set<K extends keyof CRMConfig>(key: K, value: CRMConfig[K]) {
    setCfg((c) => ({ ...c, [key]: value }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const res = await client.put({ url: BASE, body: cfg });
      if (res.error) throw new Error("save_failed");
      const data = res.data as { config: CRMConfig | null } | undefined;
      if (data?.config) setCfg({ ...EMPTY, ...data.config });
      setExists(true);
      toast.success("CRM settings saved");
    } catch {
      toast.error("Failed to save CRM settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await client.delete({ url: BASE });
      setCfg(EMPTY);
      setExists(false);
      toast.success("CRM disconnected");
    } catch {
      toast.error("Failed to disconnect");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await client.post({
        url: `${BASE}/test`,
        body: { phone: testPhone.trim() },
      });
      if (res.error) throw new Error("test_failed");
      const data = res.data as { ok: boolean; detail: string } | undefined;
      if (data?.ok) toast.success(`Connected — test contact synced (${data.detail})`);
      else toast.error(`Test failed: ${data?.detail ?? "unknown error"}`);
    } catch {
      toast.error("Test failed — save a valid config first");
    } finally {
      setTesting(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <form onSubmit={handleSave} className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Automatically push each call to your CRM — upsert the contact and log the
        outcome, recording, transcript and sentiment as a note. Bring your own CRM
        account and API token.
      </p>

      <div className="flex items-center justify-between rounded-md border p-3">
        <div>
          <Label htmlFor="crm-enabled" className="font-medium">
            Enabled
          </Label>
          <p className="text-xs text-muted-foreground">
            Auto-sync after calls. Turn off to pause without losing settings.
          </p>
        </div>
        <Switch
          id="crm-enabled"
          checked={cfg.enabled}
          onCheckedChange={(v) => set("enabled", v)}
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="crm-provider">CRM</Label>
          <Select value={cfg.provider} onValueChange={(v) => set("provider", v)}>
            <SelectTrigger id="crm-provider">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="gohighlevel">GoHighLevel</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="crm-location">Location ID</Label>
          <Input
            id="crm-location"
            placeholder="GHL sub-account location id"
            value={cfg.location_id}
            onChange={(e) => set("location_id", e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="crm-key">API token</Label>
        <Input
          id="crm-key"
          type="password"
          placeholder="GoHighLevel Private Integration Token"
          value={cfg.api_key}
          onChange={(e) => set("api_key", e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Stored encrypted and shown masked. Leave the masked value to keep the
          current token.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="crm-dispositions">Only sync for dispositions</Label>
          <Input
            id="crm-dispositions"
            placeholder="INTERESTED, XFER (blank = all)"
            value={cfg.trigger_dispositions.join(", ")}
            onChange={(e) =>
              set(
                "trigger_dispositions",
                e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="crm-minsec">Minimum call seconds</Label>
          <Input
            id="crm-minsec"
            type="number"
            min={0}
            value={cfg.min_call_seconds}
            onChange={(e) => set("min_call_seconds", Number(e.target.value) || 0)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="crm-sentiments">Only sync if sentiment matches</Label>
        <Input
          id="crm-sentiments"
          placeholder="interested, positive (blank = any sentiment)"
          value={cfg.trigger_sentiments.join(", ")}
          onChange={(e) =>
            set(
              "trigger_sentiments",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
        <p className="text-xs text-muted-foreground">
          e.g. only push leads who sounded interested to your CRM.
        </p>
      </div>

      <div className="flex gap-2">
        <Button type="submit" disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </Button>
        {exists && (
          <Button
            type="button"
            variant="destructive"
            disabled={saving}
            onClick={handleDelete}
          >
            Disconnect
          </Button>
        )}
      </div>

      {exists && (
        <>
          <Separator />
          <div className="space-y-2">
            <Label htmlFor="crm-test">Test connection</Label>
            <div className="flex gap-2">
              <Input
                id="crm-test"
                placeholder="Test phone e.g. 9876543210"
                value={testPhone}
                onChange={(e) => setTestPhone(e.target.value)}
              />
              <Button
                type="button"
                variant="secondary"
                disabled={testing}
                onClick={handleTest}
              >
                <Send className="mr-1 h-4 w-4" />
                {testing ? "Testing..." : "Test"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Upserts a test contact into your CRM. Save your changes first.
            </p>
          </div>
        </>
      )}
    </form>
  );
}
