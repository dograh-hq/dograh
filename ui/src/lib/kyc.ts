/**
 * Small fetch wrapper for the VoiceLink KYC routes (`/api/v1/kyc/*`).
 *
 * These routes are not part of the generated OpenAPI client yet, so this
 * mirrors the client's base-URL resolution and Bearer-token convention.
 */

export interface KycStatus {
  enabled: boolean;
  client_id_configured?: boolean;
  has_voicelink_config?: boolean;
  kyc_status?: string | null;
  pan_verified?: boolean | null;
  aadhaar_verified?: boolean | null;
  gst_verified?: boolean | null;
  is_complete?: boolean | null;
  current_step?: number | string | null;
  account_type?: string | null;
}

export interface KycActionResult {
  message?: string | null;
  data: Record<string, unknown>;
}

export interface KycStep1Body {
  term_and_condition: boolean;
  account_type: "individual" | "business";
  business_name?: string;
  full_name: string;
  email: string;
  phone: string;
  billing_address: string;
}

export interface KycStep2Body {
  pan_holder_name: string;
  pan_number: string;
}

export interface KycStep3Body {
  redirect_url?: string;
}

export interface KycStep4Body {
  gst_number: string;
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

async function kycFetch<T>(
  token: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${backendUrl()}/api/v1/kyc${path}`, {
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

function post<T>(token: string, path: string, body: unknown): Promise<T> {
  return kycFetch<T>(token, path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const getKycStatus = (token: string) =>
  kycFetch<KycStatus>(token, "/status");

export const submitKycStep1 = (token: string, body: KycStep1Body) =>
  post<KycActionResult>(token, "/step-1", body);

export const submitKycStep2 = (token: string, body: KycStep2Body) =>
  post<KycActionResult>(token, "/step-2", body);

export const submitKycStep3 = (token: string, body: KycStep3Body) =>
  post<KycActionResult>(token, "/step-3", body);

export const submitKycStep4 = (token: string, body: KycStep4Body) =>
  post<KycActionResult>(token, "/step-4", body);

export const submitKycFinal = (token: string) =>
  post<KycActionResult>(token, "/final-submit", {});
