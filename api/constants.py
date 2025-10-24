import os
from pathlib import Path

# Absolute path to the project root directory (i.e. the directory containing
# the top-level api/ package). Having a single canonical location helps
# when constructing file-system paths elsewhere in the codebase.
APP_ROOT_DIR: Path = Path(__file__).resolve().parent

FILLER_SOUND_PROBABILITY = 0.0

VOICEMAIL_RECORDING_DURATION = 5.0

# Configuration constants
ENABLE_SMART_TURN = os.getenv("ENABLE_SMART_TURN", "false").lower() == "true"
ENABLE_TRACING = os.getenv("ENABLE_TRACING", "false").lower() == "true"
ENABLE_RNNOISE = os.getenv("ENABLE_RNNOISE", "false").lower() == "true"

BACKEND_API_ENDPOINT = os.getenv("BACKEND_API_ENDPOINT", None)

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "oss")
DOGRAH_MPS_SECRET_KEY = os.getenv("DOGRAH_MPS_SECRET_KEY", None)
MPS_API_URL = os.getenv("MPS_API_URL", "https://services.dograh.com")

# Storage Configuration
ENABLE_AWS_S3 = os.getenv("ENABLE_AWS_S3", "false").lower() == "true"

# MinIO Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "voice-audio")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# AWS S3 Configuration
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

# Sentry configuration
SENTRY_DSN = os.getenv("SENTRY_DSN")


ENABLE_ARI_STASIS = os.getenv("ENABLE_ARI_STASIS", "false").lower() == "true"
SERIALIZE_LOG_OUTPUT = os.getenv("SERIALIZE_LOG_OUTPUT", "false").lower() == "true"
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "false").lower() == "true"
