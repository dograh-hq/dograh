// Shared dropdown options + lead source/kind types for the lead-gen forms.

export type LeadSource =
  | "sidebar"
  | "billing_card"
  | "billing_custom_pricing"
  | "builder_nudge"
  | "hire_expert"
  | "onboarding"
  | "pricing_custom_volume"
  | "landing_contact";

export type LeadKind = "hire_expert" | "enterprise";

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
];

// Enterprise deployment need (conditional — see ENTERPRISE_DEPLOYMENT_SOURCES).
export const ENTERPRISE_DEPLOYMENT_OPTIONS = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "maybe", label: "Maybe" },
] as const;

// ---------------------------------------------------------------------------
// Post-signup onboarding form options
// ---------------------------------------------------------------------------

// Onboarding: where do you plan to use this (highest-signal question — keep exact).
export const ONBOARDING_USAGE_CONTEXT_OPTIONS = [
  { value: "for_my_clients", label: "For my clients" },
  { value: "for_my_company", label: "For my company" },
  { value: "personal", label: "Personal use case" },
  { value: "exploring", label: "Just exploring" },
] as const;

// Onboarding: what best describes you.
export const ONBOARDING_PERSONA_OPTIONS = [
  { value: "enterprise_midmarket", label: "Enterprise / Mid-Market" },
  { value: "agency", label: "Agency / consultancy building for clients" },
  { value: "local_business", label: "Local business" },
  { value: "startup", label: "Startup" },
  { value: "solo", label: "Solo founder / builder" },
] as const;

// Persona values that unlock the on-prem conditional question.
export const ONBOARDING_ONPREM_PERSONAS: readonly string[] = ["enterprise_midmarket"];

// Onboarding: on-prem deployment need (conditional on Enterprise/Mid-Market).
export const ONBOARDING_ONPREM_OPTIONS = [
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
  { value: "not_sure", label: "Not sure" },
] as const;
