import os
from pathlib import Path

from api.enums import Environment

ENVIRONMENT = os.getenv("ENVIRONMENT", Environment.LOCAL.value)
# Absolute path to the project root directory (i.e. the directory containing
# the top-level api/ package). Having a single canonical location helps
# when constructing file-system paths elsewhere in the codebase.
APP_ROOT_DIR: Path = Path(__file__).resolve().parent

FILLER_SOUND_PROBABILITY = 0.0

VOICEMAIL_RECORDING_DURATION = 5.0

# Langfuse Configuration
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")

# URLs for deployment
#
# PUBLIC_BASE_URL is the single canonical origin a deployment is reached at
# (scheme + host, e.g. https://203-0-113-10.sslip.io). For a standard single-host
# install it is the only endpoint value an operator sets — the per-subsystem URLs
# below derive from it (and from PUBLIC_HOST for the TURN/ICE host). Each derived
# var can still be set explicitly to override it for a split deployment.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or None
PUBLIC_HOST = os.getenv("PUBLIC_HOST") or None

# Public URL the backend builds webhook/callback/embed links from. Derives from
# PUBLIC_BASE_URL (public IP / domain), falling back to localhost for local dev.
# When this is a non-public address (localhost or a private/reserved IP) the host
# isn't reachable from the internet, so get_backend_endpoints() resolves a running
# Cloudflare tunnel's URL at runtime instead (see api/utils/common.py).
BACKEND_API_ENDPOINT = (
    os.getenv("BACKEND_API_ENDPOINT") or PUBLIC_BASE_URL or "http://localhost:8000"
)
UI_APP_URL = os.getenv("UI_APP_URL", "http://localhost:3010")

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "oss")
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local")

# Google OAuth ("Sign in with Google"). Client id is public; the secret is server-
# side only. Redirect URI must be registered on the Google OAuth client.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "https://api.auto4you.in/api/v1/auth/google/callback"
)

# Rate limits (requests per 60s). Set any to 0 to disable that limiter.
# login/signup are IP-keyed (signup also provisions a VoiceLink client, so it's
# stricter); the public X-API-Key call-trigger surface is keyed per API key and
# kept generous so legitimate bulk usage isn't throttled.
RATE_LIMIT_LOGIN_PER_MIN = int(os.getenv("RATE_LIMIT_LOGIN_PER_MIN", "10"))
RATE_LIMIT_SIGNUP_PER_MIN = int(os.getenv("RATE_LIMIT_SIGNUP_PER_MIN", "5"))
RATE_LIMIT_PUBLIC_API_PER_MIN = int(os.getenv("RATE_LIMIT_PUBLIC_API_PER_MIN", "300"))

# Free outbound call seconds granted to a NEW org (trial). 1800 = 30 minutes.
# Existing orgs keep NULL (unlimited). Set 0 to grant nothing to new orgs.
DEFAULT_FREE_CALL_SECONDS = int(os.getenv("DEFAULT_FREE_CALL_SECONDS", "1800"))

# Razorpay (the platform billing account that collects top-up payments).
# Test-mode keys work end-to-end; swap to live keys when ready.
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
# PayU Hosted Checkout — the platform's payment gateway for credit-pack top-ups.
# Key + Salt come from the PayU dashboard (Developer Tools -> API Keys). The SALT
# is a SECRET (signs every payment) — server-side only, never sent to the browser.
# PAYU_MODE=live -> secure.payu.in; anything else (default) -> test.payu.in sandbox.
PAYU_MERCHANT_KEY = os.getenv("PAYU_MERCHANT_KEY", "")
PAYU_MERCHANT_SALT = os.getenv("PAYU_MERCHANT_SALT", "")
PAYU_MODE = os.getenv("PAYU_MODE", "test").lower()

# Voicemail-detection classifier prompt (ported from the VAPP phrase rules).
# The detector's parallel LLM branch classifies the callee's opening as
# CONVERSATION (human) or VOICEMAIL (answering machine / telecom IVR); on
# VOICEMAIL the pipeline hangs up immediately. Covers English + Hindi/Hinglish
# Indian-IVR phrasing and biases to CONVERSATION when unsure (never hang up on
# a real person). Override per workflow via voicemail_detection.system_prompt.
DEFAULT_VOICEMAIL_SYSTEM_PROMPT = """You classify the FIRST thing the called party says on an outbound phone call as either a live human ("CONVERSATION") or an automated system / answering machine / voicemail / telecom IVR ("VOICEMAIL").

Respond with ONLY one word: CONVERSATION or VOICEMAIL.

Classify as VOICEMAIL if the opening contains any of these (English, Hinglish, or Hindi), even partially:
- "voicemail", "voice mail", "leave a message", "leave your message", "record your message", "after the tone", "after the beep", "at the tone", "mailbox", "the person you are calling", "is not available", "is unavailable", "please record", "please try again later"
- Indian telecom IVR: "the number you are calling is currently not reachable", "the subscriber you are calling is not available", "is switched off", "is currently busy", "the number you have dialed is not in service", "please check the number and dial again"
- Hindi / Hinglish: "switch off hai", "abhi uplabdh nahi", "uplabdh nahi hai", "vyast hai", "sampark nahi ho pa raha", "sandesh chhodein", "sandesh chhod dijiye", "beep ke baad", "baad mein prayaas karein", "aap jis number par call kar rahe hain"
- A long, uninterrupted scripted greeting or monologue with no natural back-and-forth, or a greeting immediately followed by a beep/tone.

Classify as CONVERSATION if a real person responds naturally and briefly — e.g. "Hello?", "Hi", "Yes?", "Haan", "Hello kaun?", "Kaun bol raha hai?" — or any short conversational reply that invites dialogue.

When unsure, prefer CONVERSATION — never hang up on a real person. Respond with ONLY CONVERSATION or VOICEMAIL."""
# Plans / credit packs sold via Razorpay top-up. 1 credit = 1 call-minute, so
# `minutes` == credits granted, credited to the org's call-seconds balance.
# `features` gates self-serve surfaces by tier:
#   - api: REST API keys (Developers) — Growth & Scale only
#   - mcp: MCP server — Scale only
# The org's plan tier is derived from the highest pack it has paid for (there is
# no plan column); see api/services/plans.py. Trial orgs (no purchase) get neither.
CREDIT_PACKS = [
    {
        "id": "starter",
        "label": "Starter",
        "minutes": 300,
        "price_inr": 2399,
        "per_credit_inr": 8,
        "features": {"api": False, "mcp": False},
    },
    {
        "id": "growth",
        "label": "Growth",
        "minutes": 650,
        "price_inr": 4500,
        "per_credit_inr": 6,
        "features": {"api": True, "mcp": False},
    },
    {
        "id": "scale",
        "label": "Scale",
        "minutes": 2000,
        "price_inr": 10000,
        "per_credit_inr": 5,
        "features": {"api": True, "mcp": True},
    },
]

# Per-minute rate used to price a campaign's spend from its total call duration
# (the sum of its calls' durations). Defaults to the Starter retail rate; set
# per deployment. Spend (INR) = (consumed_seconds / 60) * this rate.
CAMPAIGN_SPEND_RATE_INR_PER_MINUTE = float(
    os.getenv("CAMPAIGN_SPEND_RATE_INR_PER_MINUTE", "8")
)

# Telephony marketplace: setup cost (in call-minutes deducted from the credit
# balance) to claim a phone number. PLACEHOLDER — founder sets the real price.
# 0 = free to claim. Unmetered (unlimited) orgs are never charged.
NUMBER_SETUP_MINUTES = int(os.getenv("NUMBER_SETUP_MINUTES", "0"))

# Single-ledger billing. When False (default) the upstream Dograh MPS model
# billing is OFF and the local call-minute credit ledger
# (organizations.free_call_seconds_remaining) is the ONLY billing system.
MANAGED_MODEL_SERVICES_ENABLED = (
    os.getenv("MANAGED_MODEL_SERVICES_ENABLED", "false").lower() == "true"
)
# Seconds held per in-flight call as a race-safe reservation, then reconciled to
# the call's true duration on completion. ~ a generous maximum call length.
CREDIT_RESERVATION_SECONDS = int(os.getenv("CREDIT_RESERVATION_SECONDS", "600"))

# Comma-separated list of emails that are promoted to superuser on
# local-auth signup/login (e.g. "owner@example.com,ops@example.com").
ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "")

# Stack Auth public client config. These are safe to expose to the browser (the
# publishable client key is public by design, and the project id is non-sensitive),
# and are served to the UI at runtime via /api/v1/health so the frontend no longer
# needs them baked into the bundle at build time.
STACK_AUTH_PROJECT_ID = os.getenv("STACK_AUTH_PROJECT_ID")
STACK_PUBLISHABLE_CLIENT_KEY = os.getenv("STACK_PUBLISHABLE_CLIENT_KEY")
DOGRAH_MPS_SECRET_KEY = os.getenv("DOGRAH_MPS_SECRET_KEY", None)
MPS_API_URL = os.getenv("MPS_API_URL", "https://services.dograh.com")

# Storage Configuration
ENABLE_AWS_S3 = os.getenv("ENABLE_AWS_S3", "false").lower() == "true"

# MinIO Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
# Full URL (scheme + host) browsers use to reach object storage. Derives from
# PUBLIC_BASE_URL (remote nginx proxies /voice-audio/ to MinIO); set explicitly
# only to point object storage at a separate origin.
MINIO_PUBLIC_ENDPOINT = (
    os.getenv("MINIO_PUBLIC_ENDPOINT") or PUBLIC_BASE_URL or "http://localhost:9000"
)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "voice-audio")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# AWS S3 Configuration
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# Optional overrides for S3-compatible backends (e.g. MinIO, rustfs, Ceph).
# S3_ENDPOINT_URL: full URL of a custom S3 endpoint (e.g. "https://s3.example.com").
#   Leave unset to use AWS's default endpoint resolution.
# S3_SIGNATURE_VERSION: botocore signature version used to sign requests and
#   presigned URLs. Defaults to None (botocore's default, currently SigV2 for
#   presigned URLs). Set to "s3v4" for S3-compatible servers that require SigV4.
# S3_ADDRESSING_STYLE: "auto" (default), "path", or "virtual". Many S3-compatible
#   servers and TLS setups require "path".
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_SIGNATURE_VERSION = os.environ.get("S3_SIGNATURE_VERSION")
S3_ADDRESSING_STYLE = os.environ.get("S3_ADDRESSING_STYLE")

# Sentry configuration
SENTRY_DSN = os.getenv("SENTRY_DSN")

# PostHog configuration
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")


ENABLE_ARI_STASIS = os.getenv("ENABLE_ARI_STASIS", "false").lower() == "true"
SERIALIZE_LOG_OUTPUT = os.getenv("SERIALIZE_LOG_OUTPUT", "false").lower() == "true"

# Logging configuration
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", None)
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

# Log rotation configuration
LOG_ROTATION_SIZE = os.getenv("LOG_ROTATION_SIZE", "100 MB")
LOG_RETENTION = os.getenv("LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("LOG_COMPRESSION", "gz")
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "true").lower() == "true"


def _get_version() -> str:
    """Read version from pyproject.toml."""
    try:
        import tomllib

        pyproject_path = APP_ROOT_DIR / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject.get("project", {}).get("version", "dev")
    except Exception:
        return "dev"


# Application version (read from pyproject.toml)
APP_VERSION = _get_version()

# Country code mapping: ISO country code -> international dialing prefix
COUNTRY_CODES = {
    "US": "1",  # United States
    "CA": "1",  # Canada
    "GB": "44",  # United Kingdom
    "IN": "91",  # India
    "AU": "61",  # Australia
    "DE": "49",  # Germany
    "FR": "33",  # France
    "BR": "55",  # Brazil
    "MX": "52",  # Mexico
    "IT": "39",  # Italy
    "ES": "34",  # Spain
    "NL": "31",  # Netherlands
    "SE": "46",  # Sweden
    "NO": "47",  # Norway
    "DK": "45",  # Denmark
    "FI": "358",  # Finland
    "CH": "41",  # Switzerland
    "AT": "43",  # Austria
    "BE": "32",  # Belgium
    "LU": "352",  # Luxembourg
    "IE": "353",  # Ireland
}

DEFAULT_ORG_CONCURRENCY_LIMIT = os.getenv("DEFAULT_ORG_CONCURRENCY_LIMIT", 2)
DEFAULT_CAMPAIGN_RETRY_CONFIG = {
    "enabled": True,
    "max_retries": 1,
    "retry_delay_seconds": 120,
    "retry_on_busy": True,
    "retry_on_no_answer": True,
    "retry_on_voicemail": False,
}


# Circuit breaker defaults for campaign call failure detection
DEFAULT_CIRCUIT_BREAKER_CONFIG = {
    "enabled": True,
    "failure_threshold": 0.5,  # 50% failure rate trips the breaker
    "window_seconds": 120,  # 2-minute sliding window
    "min_calls_in_window": 5,  # Don't trip until at least 5 outcomes
}


TURN_SECRET = os.getenv("TURN_SECRET")
# Host browsers dial for TURN/ICE. Derives from PUBLIC_HOST; set explicitly only
# when the TURN server runs on a separate host from the app.
TURN_HOST = os.getenv("TURN_HOST") or PUBLIC_HOST or "localhost"
TURN_PORT = int(os.getenv("TURN_PORT", "3478"))
TURN_TLS_PORT = int(os.getenv("TURN_TLS_PORT", "5349"))
TURN_CREDENTIAL_TTL = int(os.getenv("TURN_CREDENTIAL_TTL", "86400"))
# Diagnostic flag: when true, strip all non-relay ICE candidates from the
# answer SDP so every media path must traverse the TURN server. Use for
# verifying TURN connectivity end-to-end; expect connection failures if
# TURN is misconfigured or unreachable.
FORCE_TURN_RELAY = os.getenv("FORCE_TURN_RELAY", "false").lower() == "true"

# OSS Email/Password Auth
OSS_JWT_SECRET = os.getenv("OSS_JWT_SECRET", "change-me-in-production")
OSS_JWT_EXPIRY_HOURS = int(os.getenv("OSS_JWT_EXPIRY_HOURS", "720"))  # 30 days

TUNER_BASE_URL = os.getenv("TUNER_BASE_URL", "https://api.usetuner.ai")
