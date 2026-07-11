"""backfill org model configuration v2 from legacy user rows

Revision ID: 00b0201ad918
Revises: c52dc3cccfb0
Create Date: 2026-07-09 12:00:00.000000

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00b0201ad918"
down_revision: Union[str, None] = "c52dc3cccfb0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Organizations created before MODEL_CONFIGURATION_V2 only have per-user legacy
# MODEL_CONFIGURATION rows, and the application no longer falls back to those.
# This migration converts one member's legacy row per organization into an
# org-level v2 row. The conversion below is a frozen copy of
# convert_legacy_ai_model_configuration_to_v2 (api/services/configuration/
# ai_model_configuration.py at this revision) operating on raw JSON, so the
# migration never imports application code. API keys are deliberately carried
# over without validation: a dead key fails at call time exactly as it did
# before this migration.

_SPEED_MIN = 0.5
_SPEED_MAX = 2.0
_DEFAULT_VOICE = "default"
_DEFAULT_LANGUAGE = "multi"

# One candidate row per (organization, member with a legacy config row).
# Members are ordered the way the hosted migration script picked its
# representative user: users whose selected organization is this org first,
# then earliest created. The first member whose row converts wins.
_CANDIDATE_ROWS_SQL = """
SELECT ou.organization_id,
       uc.configuration,
       uc.last_validated_at
FROM organization_users ou
JOIN users u ON u.id = ou.user_id
JOIN user_configurations uc
  ON uc.user_id = u.id AND uc.key = 'MODEL_CONFIGURATION'
WHERE NOT EXISTS (
        SELECT 1
        FROM organization_configurations oc
        WHERE oc.organization_id = ou.organization_id
          AND oc.key = 'MODEL_CONFIGURATION_V2'
      )
ORDER BY ou.organization_id,
         (u.selected_organization_id IS NOT DISTINCT FROM ou.organization_id) DESC,
         u.created_at ASC NULLS LAST,
         u.id ASC
"""

_INSERT_SQL = """
INSERT INTO organization_configurations
    (organization_id, key, value, created_at, updated_at, last_validated_at)
VALUES
    (:organization_id, 'MODEL_CONFIGURATION_V2', CAST(:value AS json),
     now(), now(), :last_validated_at)
ON CONFLICT ON CONSTRAINT _organization_key_uc DO NOTHING
"""


def _section(configuration: dict, name: str) -> dict | None:
    value = configuration.get(name)
    return value if isinstance(value, dict) else None


def _single_api_key(service: dict) -> str | None:
    key = service.get("api_key")
    if isinstance(key, str) and key:
        return key
    if isinstance(key, list) and len(key) == 1 and isinstance(key[0], str) and key[0]:
        return key[0]
    return None


def _first_dograh_api_key(configuration: dict) -> str | None:
    for name in ("llm", "tts", "stt", "embeddings", "realtime"):
        service = _section(configuration, name)
        if service is None or service.get("provider") != "dograh":
            continue
        key = _single_api_key(service)
        if key:
            return key
    return None


def _has_dograh_provider(*services: dict | None) -> bool:
    return any(
        service is not None and service.get("provider") == "dograh"
        for service in services
    )


def _sanitized_speed(tts: dict | None) -> float:
    speed = (tts or {}).get("speed", 1.0)
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return 1.0
    if not _SPEED_MIN <= speed <= _SPEED_MAX:
        return 1.0
    return speed


def convert_legacy_configuration_to_v2(configuration: dict) -> dict | None:
    """Convert a legacy MODEL_CONFIGURATION JSON value to a v2 value.

    Returns None when the legacy row is too incomplete to have produced a
    working pipeline (such rows failed at pipeline startup before v2 too).
    """
    llm = _section(configuration, "llm")
    tts = _section(configuration, "tts")
    stt = _section(configuration, "stt")
    embeddings = _section(configuration, "embeddings")
    realtime = _section(configuration, "realtime")

    dograh_key = _first_dograh_api_key(configuration)
    if dograh_key:
        return {
            "version": 2,
            "mode": "dograh",
            "dograh": {
                "api_key": dograh_key,
                "voice": (tts or {}).get("voice") or _DEFAULT_VOICE,
                "speed": _sanitized_speed(tts),
                "language": (stt or {}).get("language") or _DEFAULT_LANGUAGE,
            },
        }

    if configuration.get("is_realtime"):
        # BYOK schemas reject dograh providers; a dograh provider without a
        # single resolvable key cannot be represented in v2.
        if realtime is None or llm is None or _has_dograh_provider(llm, embeddings):
            return None
        section: dict = {"realtime": realtime, "llm": llm}
        if embeddings is not None:
            section["embeddings"] = embeddings
        return {
            "version": 2,
            "mode": "byok",
            "byok": {"mode": "realtime", "realtime": section},
        }

    if llm is None or tts is None or stt is None:
        return None
    if _has_dograh_provider(llm, tts, stt, embeddings):
        return None
    section = {"llm": llm, "tts": tts, "stt": stt}
    if embeddings is not None:
        section["embeddings"] = embeddings
    return {
        "version": 2,
        "mode": "byok",
        "byok": {"mode": "pipeline", "pipeline": section},
    }


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(_CANDIDATE_ROWS_SQL)).mappings().all()

    backfilled: set[int] = set()
    seen: set[int] = set()
    for row in rows:
        organization_id = row["organization_id"]
        seen.add(organization_id)
        if organization_id in backfilled:
            continue

        configuration = row["configuration"]
        if isinstance(configuration, str):
            try:
                configuration = json.loads(configuration)
            except ValueError:
                continue
        if not isinstance(configuration, dict):
            continue

        try:
            v2_value = convert_legacy_configuration_to_v2(configuration)
        except Exception:
            v2_value = None
        if v2_value is None:
            continue

        connection.execute(
            sa.text(_INSERT_SQL),
            {
                "organization_id": organization_id,
                "value": json.dumps(v2_value),
                "last_validated_at": row["last_validated_at"],
            },
        )
        backfilled.add(organization_id)

    skipped = sorted(seen - backfilled)
    print(
        f"Backfilled MODEL_CONFIGURATION_V2 for {len(backfilled)} organization(s); "
        f"skipped {len(skipped)} with no convertible legacy configuration"
        + (f": {skipped}" if skipped else "")
    )


def downgrade() -> None:
    # Backfilled rows are indistinguishable from rows written by the
    # application; leaving them in place is safe for older code.
    pass
