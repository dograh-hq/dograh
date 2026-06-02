// Returns true if the email uses a common free/personal domain.
// Used to gate "work email" fields on lead forms.

const PERSONAL_EMAIL_DOMAINS = new Set([
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.in",
  "yahoo.co.uk",
  "ymail.com",
  "outlook.com",
  "hotmail.com",
  "hotmail.co.uk",
  "live.com",
  "msn.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "proton.me",
  "protonmail.com",
  "pm.me",
  "aol.com",
  "gmx.com",
  "gmx.net",
  "mail.com",
  "zoho.com",
  "yandex.com",
  "fastmail.com",
]);

export function isValidEmail(email: string): boolean {
  // Pragmatic check — not RFC-perfect, but rejects obvious garbage.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

export function isPersonalEmail(email: string): boolean {
  const at = email.trim().toLowerCase().split("@");
  if (at.length !== 2) return false;
  return PERSONAL_EMAIL_DOMAINS.has(at[1]);
}

// Convenience validator for work-email fields.
// Returns an error string, or null if valid.
export function validateWorkEmail(email: string): string | null {
  if (!email.trim()) return "Work email is required";
  if (!isValidEmail(email)) return "Please enter a valid email address";
  if (isPersonalEmail(email)) return "Please use your work email";
  return null;
}
