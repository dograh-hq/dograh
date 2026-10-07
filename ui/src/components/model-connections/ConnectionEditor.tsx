"use client";

import { useState } from "react";

import { createProviderConnection, updateProviderConnection } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";

import { connectionProviders, schemaDefaults } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import { SchemaFields } from "./SchemaFields";
import type { ModelConnectionCatalog, ProviderConnection } from "./types";

export function ConnectionEditor({ catalog, connection, onSaved }: {
    catalog: ModelConnectionCatalog;
    connection?: ProviderConnection;
    onSaved: (uuid: string) => Promise<void>;
}) {
    const providers = connectionProviders(catalog);
    const initialProvider = connection?.provider || Object.keys(providers)[0] || "";
    const [provider, setProvider] = useState(initialProvider);
    const [name, setName] = useState(connection?.name || "");
    const [settings, setSettings] = useState<Record<string, unknown>>(connection?.connection_settings || schemaDefaults({ properties: providers[initialProvider]?.connection_fields }));
    const [credentials, setCredentials] = useState<Record<string, unknown>>({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const entry = providers[provider];

    const changeProvider = (nextProvider: string) => {
        setProvider(nextProvider);
        setCredentials({});
        setSettings(schemaDefaults({ properties: providers[nextProvider]?.connection_fields }));
    };
    const changeField = (previous: Record<string, unknown>, field: string, value: unknown) => {
        const next = { ...previous, [field]: value };
        if (value === undefined) delete next[field];
        return next;
    };
    return <form className="space-y-5" onSubmit={async event => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
            // Ignore unfilled extra credential rows.
            const normalizedCredentials = Object.fromEntries(Object.entries(credentials).map(([key, value]) => [key, Array.isArray(value) ? value.map(item => typeof item === "string" ? item.trim() : item).filter(Boolean) : value]));
            const body = { name: name.trim(), connection_settings: settings, credentials: normalizedCredentials };
            const result = connection
                ? await updateProviderConnection({ path: { connection_uuid: connection.uuid }, body: { ...body, revision: connection.revision } })
                : await createProviderConnection({ body: { ...body, provider } });
            if (result.error) throw new Error(detailFromError(result.error, "Failed to save provider connection"));
            if (!result.data) throw new Error("Failed to save provider connection");
            await onSaved(result.data.uuid);
        } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to save provider connection"); }
        finally { setSaving(false); }
    }}>
        <div className="space-y-2">
            <h1 className="text-2xl font-bold">{connection ? "Edit Provider Connection" : "Add Provider"}</h1>
            <p className="text-sm text-muted-foreground">Connect a provider account, then reuse it in your model configurations.</p>
        </div>
        {error && <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
        <div className="space-y-1.5">
            <Label htmlFor="connection-provider">Provider</Label>
            <ConfigurationSelect id="connection-provider" value={provider} required disabled={Boolean(connection)} onValueChange={changeProvider}
                options={Object.entries(providers).map(([key, schema]) => ({ value: key, label: schema.title || key }))} />
        </div>
        <div className="space-y-1.5"><Label htmlFor="connection-name">Connection name</Label>
            <Input id="connection-name" required maxLength={128} placeholder="Production account" value={name} onChange={event => setName(event.target.value)} /></div>
        {entry && <>
            {Object.keys(entry.connection_fields).length > 0 && <fieldset className="space-y-3">
                <legend className="mb-2 text-sm font-semibold">Connection settings</legend>
                <SchemaFields key={`connection-${provider}`} schema={{ properties: entry.connection_fields, required: entry.connection_required }} values={settings}
                    onChange={(field, value) => setSettings(previous => changeField(previous, field, value))} />
            </fieldset>}
            {Object.keys(entry.credential_fields).length > 0 && <fieldset className="space-y-3">
                <legend className="mb-2 text-sm font-semibold">Credentials</legend>
                <SchemaFields key={`credentials-${provider}`} secret schema={{ properties: entry.credential_fields, required: entry.credential_required }} values={credentials}
                    configuredFields={connection?.configured_credentials} onChange={(field, value) => setCredentials(previous => changeField(previous, field, value))} />
            </fieldset>}
        </>}
        <div className="flex justify-end"><Button type="submit" disabled={saving || !entry || !name.trim()}>{saving ? "Validating and saving…" : "Save Connection"}</Button></div>
    </form>;
}
