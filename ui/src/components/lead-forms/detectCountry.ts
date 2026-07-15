// Best-effort country detection for lead provenance — no permission prompt, no
// network call, no precise geolocation. Primary signal: the browser's IANA timezone
// → ISO 3166-1 country (location-based; timezones shared by several countries resolve
// to the larger/likelier one). Fallback: the browser locale's region. Returns a
// human-readable country name (e.g. "India") — it goes in the founders-email subject —
// or undefined if nothing resolves. Sent silently in the form body; never shown.

// IANA timezone → ISO 3166-1 alpha-2. Curated to the common business regions; anything
// not listed falls back to the locale region below. Shared zones → larger country.
const TZ_TO_ISO: Record<string, string> = {
  // United States
  "America/New_York": "US", "America/Detroit": "US", "America/Chicago": "US",
  "America/Denver": "US", "America/Phoenix": "US", "America/Los_Angeles": "US",
  "America/Anchorage": "US", "America/Adak": "US", "America/Boise": "US",
  "America/Indiana/Indianapolis": "US", "Pacific/Honolulu": "US",
  // Canada
  "America/Toronto": "CA", "America/Montreal": "CA", "America/Vancouver": "CA",
  "America/Edmonton": "CA", "America/Winnipeg": "CA", "America/Halifax": "CA",
  "America/St_Johns": "CA", "America/Regina": "CA",
  // Mexico & Central/South America
  "America/Mexico_City": "MX", "America/Tijuana": "MX", "America/Monterrey": "MX",
  "America/Cancun": "MX", "America/Sao_Paulo": "BR", "America/Bahia": "BR",
  "America/Argentina/Buenos_Aires": "AR", "America/Santiago": "CL",
  "America/Bogota": "CO", "America/Lima": "PE", "America/Caracas": "VE",
  "America/Guayaquil": "EC", "America/Montevideo": "UY",
  // United Kingdom & Ireland
  "Europe/London": "GB", "Europe/Dublin": "IE",
  // Europe
  "Europe/Paris": "FR", "Europe/Berlin": "DE", "Europe/Madrid": "ES",
  "Europe/Rome": "IT", "Europe/Amsterdam": "NL", "Europe/Brussels": "BE",
  "Europe/Zurich": "CH", "Europe/Vienna": "AT", "Europe/Stockholm": "SE",
  "Europe/Oslo": "NO", "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI",
  "Europe/Warsaw": "PL", "Europe/Prague": "CZ", "Europe/Budapest": "HU",
  "Europe/Bucharest": "RO", "Europe/Athens": "GR", "Europe/Lisbon": "PT",
  "Europe/Moscow": "RU", "Europe/Kiev": "UA", "Europe/Kyiv": "UA",
  "Europe/Istanbul": "TR",
  // South Asia
  "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Karachi": "PK",
  "Asia/Dhaka": "BD", "Asia/Colombo": "LK", "Asia/Kathmandu": "NP",
  // Middle East
  "Asia/Dubai": "AE", "Asia/Riyadh": "SA", "Asia/Qatar": "QA",
  "Asia/Kuwait": "KW", "Asia/Jerusalem": "IL", "Asia/Tehran": "IR",
  "Asia/Baghdad": "IQ", "Asia/Amman": "JO",
  // East Asia
  "Asia/Shanghai": "CN", "Asia/Hong_Kong": "HK", "Asia/Taipei": "TW",
  "Asia/Tokyo": "JP", "Asia/Seoul": "KR",
  // Southeast Asia
  "Asia/Singapore": "SG", "Asia/Bangkok": "TH", "Asia/Jakarta": "ID",
  "Asia/Kuala_Lumpur": "MY", "Asia/Manila": "PH", "Asia/Ho_Chi_Minh": "VN",
  // Oceania
  "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Brisbane": "AU",
  "Australia/Perth": "AU", "Australia/Adelaide": "AU", "Pacific/Auckland": "NZ",
  // Africa
  "Africa/Johannesburg": "ZA", "Africa/Lagos": "NG", "Africa/Cairo": "EG",
  "Africa/Nairobi": "KE", "Africa/Casablanca": "MA", "Africa/Accra": "GH",
};

// Resolve the browser locale's region (e.g. "en-GB" → "GB"), maximizing likely subtags
// for bare languages (e.g. "en" → "US"). Returns an ISO alpha-2 or undefined.
function localeRegion(): string | undefined {
  if (typeof navigator === "undefined" || !navigator.language) return undefined;
  try {
    return new Intl.Locale(navigator.language).maximize().region ?? undefined;
  } catch {
    return undefined;
  }
}

// Raw IANA browser timezone (e.g. "America/New_York"). A neutral analytics value sent with
// leads; the BACKEND alone decides anything from it — this app holds no such logic.
// Returns undefined if Intl is unavailable.
export function detectTimezone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

export function detectCountry(): string | undefined {
  let iso: string | undefined;
  try {
    iso = TZ_TO_ISO[Intl.DateTimeFormat().resolvedOptions().timeZone];
  } catch {
    // Intl unavailable — fall through to the locale region.
  }
  iso = iso || localeRegion();
  if (!iso) return undefined;
  try {
    // Human-readable name for the founders-email subject (e.g. "IN" → "India").
    return new Intl.DisplayNames(["en"], { type: "region" }).of(iso) ?? iso;
  } catch {
    return iso;
  }
}
