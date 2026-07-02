/**
 * Small fetch wrapper for the superuser admin Clients routes
 * (`/api/v1/admin/clients*`).
 *
 * These routes are not part of the generated OpenAPI client yet, so this
 * mirrors the client's base-URL resolution and Bearer-token convention
 * (same pattern as lib/kyc.ts).
 */

export type VoiceLinkLiveState =
  | "active"
  | "missing"
  | "unconfigured"
  | "unknown";

export interface AdminClient {
  organization_id: number;
  organization_name: string;
  owner_user_id?: number | null;
  owner_email?: string | null;
  owner_provider_id?: string | null;
  created_at?: string | null;
  voicelink_status?: string | null;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
  has_voicelink_config: boolean;
  did_number?: string | null;
  // Live reconciliation against VoiceLink.
  live_state: VoiceLinkLiveState;
  live_client_id?: string | null;
  // Remaining call-seconds balance; null = unmetered (unlimited).
  credits_seconds_remaining?: number | null;
}

export interface AdminClientsListResult {
  clients: AdminClient[];
}

export interface RetryProvisionResult {
  voicelink_status: string;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
}

export interface CreateClientResult {
  action: string; // "linked" | "created"
  voicelink_status: string;
  voicelink_client_id?: string | null;
  voicelink_username?: string | null;
  voicelink_error?: string | null;
}

export interface AssignDidBody {
  did_number: string;
  client_id?: string;
}

export interface AssignDidResult {
  configuration_id: number;
  created: boolean;
  did_number: string;
  client_id?: string | null;
}

export interface GrantCreditsResult {
  organization_id: number;
  granted_seconds: number;
  credits_seconds_remaining?: number | null;
}

// "ok" = fetched from VoiceLink | "no_client" = org has no VoiceLink client
// id | "disabled" = reseller credentials unset on the backend.
export type AdminKycState = "ok" | "no_client" | "disabled";

export interface AdminClientKycStatus {
  status: AdminKycState;
  enabled: boolean;
  client_id_configured?: boolean;
  has_voicelink_config?: boolean;
  client_id?: string | null;
  kyc_status?: string | null;
  pan_verified?: boolean | null;
  aadhaar_verified?: boolean | null;
  gst_verified?: boolean | null;
  is_complete?: boolean | null;
  current_step?: number | string | null;
  account_type?: string | null;
}

function backendUrl(): string {
  return (
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    (typeof window !== "undefined" ? window.location.origin : "")
  );
}

function detailFromBody(body: unknown): string {
  const e = body as { detail?: unknown };
  if (typeof e?.detail === "string") return e.detail;
  if (Array.isArray(e?.detail) && e.detail.length > 0) {
    const first = e.detail[0] as { msg?: string };
    if (first?.msg) return first.msg;
  }
  return "Request failed";
}

async function adminFetch<T>(
  token: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${backendUrl()}/api/v1/admin/clients${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...(init?.headers ?? {}),
    },
  });
  let body: unknown = {};
  try {
    body = await res.json();
  } catch {
    // Non-JSON response body — fall through to the generic error below.
  }
  if (!res.ok) throw new Error(detailFromBody(body));
  return body as T;
}

export const listAdminClients = (token: string) =>
  adminFetch<AdminClientsListResult>(token, "");

export const retryProvisionClient = (
  token: string,
  organizationId: number,
  password: string,
) =>
  adminFetch<RetryProvisionResult>(
    token,
    `/${organizationId}/retry-provision`,
    { method: "POST", body: JSON.stringify({ password }) },
  );

export const createClientForOrg = (
  token: string,
  organizationId: number,
  password?: string,
) =>
  adminFetch<CreateClientResult>(token, `/${organizationId}/create`, {
    method: "POST",
    body: JSON.stringify(password ? { password } : {}),
  });

export const assignDidToClient = (
  token: string,
  organizationId: number,
  body: AssignDidBody,
) =>
  adminFetch<AssignDidResult>(token, `/${organizationId}/assign-did`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const grantCreditsToClient = (
  token: string,
  organizationId: number,
  minutes: number,
) =>
  adminFetch<GrantCreditsResult>(token, `/${organizationId}/grant-credits`, {
    method: "POST",
    body: JSON.stringify({ minutes }),
  });

export const getClientKycStatus = (token: string, organizationId: number) =>
  adminFetch<AdminClientKycStatus>(token, `/${organizationId}/kyc-status`);
