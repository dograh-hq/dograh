// Shared dropdown options + lead source/kind types for the lead-gen forms.

export type LeadSource =
  | "sidebar"
  | "billing_card"
  | "billing_custom_pricing"
  | "builder_nudge"
  | "hire_expert"
  | "pricing_custom_volume"
  | "landing_contact"
  | "auth_page";

export type LeadKind = "hire_expert" | "enterprise";

// Provenance stamped by the in-app forms (analytics only; the marketing site and
// server use "website"). Derived from AppConfig deploymentMode: cloud → "cloud_app",
// otherwise "oss_app". OSS submits via the public no-token endpoints.
export type LeadOrigin = "cloud_app" | "oss_app";

// Monthly call-volume buckets. Values MUST match the backend qualifier enum
// (user_onboarding flows): "0-5k" | "5k-100k" | "100k+" | "not-sure".
export const VOLUME_OPTIONS = [
  { value: "0-5k", label: "0–5k" },
  { value: "5k-100k", label: "5k–100k" },
  { value: "100k+", label: "100k+" },
  { value: "not-sure", label: "Not sure" },
] as const;

// Hire-an-Expert expected monthly call volume (shared bucket set).
export const HIRE_VOLUME_OPTIONS = VOLUME_OPTIONS;

// Enterprise monthly call volume (shared bucket set).
export const ENTERPRISE_VOLUME_OPTIONS = VOLUME_OPTIONS;

// Lead sources for which the Enterprise modal surfaces the conditional
// "Need enterprise deployment (SSO, on-prem, data residency)?" question.
// Other entry points hide it and default the payload to "yes".
export const ENTERPRISE_DEPLOYMENT_SOURCES: readonly LeadSource[] = [
  "billing_custom_pricing",
  "pricing_custom_volume",
  "landing_contact",
  "auth_page",
];

// Enterprise deployment need (conditional — see ENTERPRISE_DEPLOYMENT_SOURCES).
export const ENTERPRISE_DEPLOYMENT_OPTIONS = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "maybe", label: "Maybe" },
] as const;
