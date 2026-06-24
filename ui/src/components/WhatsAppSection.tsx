"use client";

import { Plus, Send, Trash2 } from "lucide-react";
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

// Mirrors api/schemas/whatsapp_config.py::WhatsAppConfig. The generated SDK isn't
// regenerated here (needs a running API), so these routes are called through the
// shared hey-api `client` with explicit URLs — same baseUrl + auth interceptor.
interface WhatsAppConfig {
  enabled: boolean;
  provider: string;
  api_key: string;
  sender_name: string;
  campaign_name: string;
  template_params: string[];
  trigger_dispositions: string[];
  trigger_sentiments: string[];
  min_call_seconds: number;
  media_url: string | null;
  media_filename: string | null;
}

const EMPTY: WhatsAppConfig = {
  enabled: false,
  provider: "aisensy",
  api_key: "",
  sender_name: "",
  campaign_name: "",
  template_params: [],
  trigger_dispositions: [],
  trigger_sentiments: [],
  min_call_seconds: 0,
  media_url: "",
  media_filename: "",
};

const WA_BASE = "/api/v1/organizations/whatsapp-config";

// Tokens the post-call sender substitutes into each template param before sending.
const TOKENS = [
  "{{called_number}}",
  "{{caller_number}}",
  "{{disposition}}",
  "{{recording_url}}",
  "{{transcript_url}}",
  "{{var.<lead_column>}}",
];

export function WhatsAppSection() {
  const { user, loading: authLoading } = useAuth();
  const [cfg, setCfg] = useState<WhatsAppConfig>(EMPTY);
  const [exists, setExists] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [testing, setTesting] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    fetchConfig();
  }, [authLoading, user]);

  async function fetchConfig() {
    try {
      const res = await client.get({ url: WA_BASE });
      const data = res.data as { config: WhatsAppConfig | null } | undefined;
      if (data?.config) {
        setExists(true);
        setCfg({
          ...EMPTY,
          ...data.config,
          media_url: data.config.media_url ?? "",
          media_filename: data.config.media_filename ?? "",
        });
      }
    } catch {
      // Nothing configured yet — that's fine.
    } finally {
      setLoading(false);
    }
  }

  function setField<K extends keyof WhatsAppConfig>(key: K, value: WhatsAppConfig[K]) {
    setCfg((c) => ({ ...c, [key]: value }));
  }

  function setParam(i: number, value: string) {
    setCfg((c) => {
      const next = [...c.template_params];
      next[i] = value;
      return { ...c, template_params: next };
    });
  }

  function addParam() {
    setCfg((c) => ({ ...c, template_params: [...c.template_params, ""] }));
  }

  function removeParam(i: number) {
    setCfg((c) => ({
      ...c,
      template_params: c.template_params.filter((_, idx) => idx !== i),
    }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const res = await client.put({ url: WA_BASE, body: cfg });
      if (res.error) throw new Error("save_failed");
      const data = res.data as { config: WhatsAppConfig | null } | undefined;
      if (data?.config) {
        setCfg({
          ...EMPTY,
          ...data.config,
          media_url: data.config.media_url ?? "",
          media_filename: data.config.media_filename ?? "",
        });
      }
      setExists(true);
      toast.success("WhatsApp settings saved");
    } catch {
      toast.error("Failed to save WhatsApp settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setSaving(true);
    try {
      await client.delete({ url: WA_BASE });
      setCfg(EMPTY);
      setExists(false);
      toast.success("WhatsApp settings removed");
    } catch {
      toast.error("Failed to remove WhatsApp settings");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    if (!testTo.trim()) {
      toast.error("Enter a phone number to test");
      return;
    }
    setTesting(true);
    try {
      const res = await client.post({
        url: `${WA_BASE}/test`,
        body: { destination: testTo.trim() },
      });
      if (res.error) throw new Error("test_failed");
      const data = res.data as { ok: boolean; detail: string } | undefined;
      if (data?.ok) {
        toast.success(`Test message submitted (${data.detail})`);
      } else {
        toast.error(`Test rejected: ${data?.detail ?? "unknown error"}`);
      }
    } catch {
      toast.error("Test failed — save a valid config first");
    } finally {
      setTesting(false);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }

  return (
    <form onSubmit={handleSave} className="space-y-5">
      <p className="text-sm text-muted-foreground">
        Send an approved WhatsApp template (with an optional document) to the lead
        after each call completes. Bring your own provider account and API key.
      </p>

      <div className="flex items-center justify-between rounded-md border p-3">
        <div>
          <Label htmlFor="wa-enabled" className="font-medium">
            Enabled
          </Label>
          <p className="text-xs text-muted-foreground">
            Auto-send after calls. Turn off to pause without losing settings.
          </p>
        </div>
        <Switch
          id="wa-enabled"
          checked={cfg.enabled}
          onCheckedChange={(v) => setField("enabled", v)}
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="wa-provider">Provider</Label>
          <Select value={cfg.provider} onValueChange={(v) => setField("provider", v)}>
            <SelectTrigger id="wa-provider">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="aisensy">AiSensy</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="wa-sender">Sender / brand name</Label>
          <Input
            id="wa-sender"
            placeholder="auto4you"
            value={cfg.sender_name}
            onChange={(e) => setField("sender_name", e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="wa-apikey">API key</Label>
        <Input
          id="wa-apikey"
          type="password"
          placeholder="Your AiSensy API key"
          value={cfg.api_key}
          onChange={(e) => setField("api_key", e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Stored encrypted and shown masked. Leave the masked value to keep the
          current key.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="wa-campaign">Campaign / template name</Label>
        <Input
          id="wa-campaign"
          placeholder="post_call_followup"
          value={cfg.campaign_name}
          onChange={(e) => setField("campaign_name", e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          The AiSensy API Campaign that binds your approved Meta template.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Template parameters</Label>
        <p className="text-xs text-muted-foreground">
          Fill {"{{1}}, {{2}}…"} in order. Each value may contain tokens:{" "}
          {TOKENS.join("  ")}
        </p>
        {cfg.template_params.map((p, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="w-8 text-xs text-muted-foreground">{`{{${i + 1}}}`}</span>
            <Input
              value={p}
              placeholder={i === 0 ? "{{var.name}}" : "value or {{token}}"}
              onChange={(e) => setParam(i, e.target.value)}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => removeParam(i)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" onClick={addParam}>
          <Plus className="mr-1 h-4 w-4" /> Add parameter
        </Button>
      </div>

      <Separator />

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="wa-media-url">Document URL (optional)</Label>
          <Input
            id="wa-media-url"
            placeholder="https://… or {{recording_url}}"
            value={cfg.media_url ?? ""}
            onChange={(e) => setField("media_url", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="wa-media-name">Document filename (optional)</Label>
          <Input
            id="wa-media-name"
            placeholder="quote.pdf"
            value={cfg.media_filename ?? ""}
            onChange={(e) => setField("media_filename", e.target.value)}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="wa-dispositions">Only send for dispositions</Label>
          <Input
            id="wa-dispositions"
            placeholder="XFER, COMPLETED (blank = all)"
            value={cfg.trigger_dispositions.join(", ")}
            onChange={(e) =>
              setField(
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
          <Label htmlFor="wa-minsec">Minimum call seconds</Label>
          <Input
            id="wa-minsec"
            type="number"
            min={0}
            value={cfg.min_call_seconds}
            onChange={(e) =>
              setField("min_call_seconds", Number(e.target.value) || 0)
            }
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="wa-sentiments">Only send if sentiment matches</Label>
        <Input
          id="wa-sentiments"
          placeholder="interested, positive (blank = any sentiment)"
          value={cfg.trigger_sentiments.join(", ")}
          onChange={(e) =>
            setField(
              "trigger_sentiments",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
        <p className="text-xs text-muted-foreground">
          e.g. send the brochure only to leads who sounded interested.
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
            Remove
          </Button>
        )}
      </div>

      {exists && (
        <>
          <Separator />
          <div className="space-y-2">
            <Label htmlFor="wa-test">Send a test</Label>
            <div className="flex gap-2">
              <Input
                id="wa-test"
                placeholder="919876543210"
                value={testTo}
                onChange={(e) => setTestTo(e.target.value)}
              />
              <Button
                type="button"
                variant="secondary"
                disabled={testing}
                onClick={handleTest}
              >
                <Send className="mr-1 h-4 w-4" />
                {testing ? "Sending..." : "Send test"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Uses the saved template. Save your changes first.
            </p>
          </div>
        </>
      )}
    </form>
  );
}
