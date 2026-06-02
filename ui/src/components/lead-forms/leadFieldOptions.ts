// Shared dropdown options + lead source/kind types for the lead-gen forms.

export type LeadSource =
  | "sidebar"
  | "billing_card"
  | "builder_nudge"
  | "topup"
  | "hire_expert";

export type LeadKind = "topup" | "hire_expert" | "enterprise";

// Top-up: expected monthly call volume. ">20k" unlocks the volume-pricing block.
export const TOPUP_VOLUME_OPTIONS = [
  { value: "0-5k", label: "0–5k calls/month" },
  { value: "5k-20k", label: "5k–20k calls/month" },
  { value: ">20k", label: ">20k calls/month" },
] as const;

// The value that gates the volume-pricing qualifier block.
export const VOLUME_PRICING_GATE = ">20k";

// Top-up volume-pricing qualifier: company size (small-business scale).
export const TOPUP_COMPANY_SIZE_OPTIONS = [
  { value: "only_me", label: "Only me" },
  { value: "2-10", label: "2–10" },
  { value: "10-100", label: "10–100" },
  { value: "100-1000", label: "100–1000" },
  { value: "1000+", label: "1000+" },
] as const;

// Hire-an-Expert timeline.
export const HIRE_TIMELINE_OPTIONS = [
  { value: "asap", label: "ASAP" },
  { value: "2-4_weeks", label: "2–4 weeks" },
  { value: "1-2_months", label: "1–2 months" },
  { value: "flexible", label: "Flexible" },
  { value: "exploring", label: "Exploring" },
] as const;

// Hire-an-Expert expected monthly call volume.
export const HIRE_VOLUME_OPTIONS = [
  { value: "0-5k", label: "0–5k" },
  { value: "5k-100k", label: "5k–100k" },
  { value: "100k+", label: "100k+" },
  { value: "not_sure", label: "Not sure" },
] as const;

// Hire-an-Expert current stage.
export const HIRE_STAGE_OPTIONS = [
  { value: "live_process", label: "Have a live process we want to automate" },
  { value: "idea_no_process", label: "Have an idea, no process yet" },
  { value: "researching", label: "Just researching" },
  { value: "built_need_help", label: "Already built something, need help fixing" },
] as const;

// Enterprise industry.
export const ENTERPRISE_INDUSTRY_OPTIONS = [
  { value: "financial_services", label: "Financial services" },
  { value: "healthcare", label: "Healthcare" },
  { value: "insurance", label: "Insurance" },
  { value: "government", label: "Government" },
  { value: "telecom", label: "Telecom" },
  { value: "bpo", label: "BPO" },
  { value: "other", label: "Other" },
] as const;

// Enterprise company size (enterprise scale — intentionally different from top-up's).
export const ENTERPRISE_COMPANY_SIZE_OPTIONS = [
  { value: "50-200", label: "50–200" },
  { value: "200-1000", label: "200–1000" },
  { value: "1000-5000", label: "1000–5000" },
  { value: "5000+", label: "5000+" },
] as const;

// Enterprise timeline.
export const ENTERPRISE_TIMELINE_OPTIONS = [
  { value: "this_quarter", label: "This quarter" },
  { value: "next_quarter", label: "Next quarter" },
  { value: "6_months", label: "6 months" },
  { value: "exploring", label: "Exploring" },
] as const;
