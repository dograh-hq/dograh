import os

os.environ.setdefault("DATA_EXPLORER_DATABASE_READONLY_URL", "postgresql+asyncpg://readonly@example.invalid/calmos")
os.environ.setdefault("DATA_EXPLORER_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("DATA_EXPLORER_AUDIT_LOG_PATH", "/tmp/calmos-data-explorer-test-audit.jsonl")
