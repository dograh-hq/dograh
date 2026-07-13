"""Flow variables and template rendering for Dograh workflows."""

from __future__ import annotations

import re
from typing import Any

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.-]*)\s*\}\}")


def build_runtime_variables(
    config: dict[str, Any] | None,
    *,
    memory_variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stable variables available to every flow node."""
    config = config or {}
    channel = str(config.get("channel") or "").strip()
    sender_phone = str(config.get("sender_phone") or "").strip()
    user_id = str(config.get("user_id") or sender_phone or "").strip()
    session_id = str(config.get("session_id") or "").strip()
    deploy_id = str(config.get("deploy_id") or "").strip()
    org_id = str(config.get("org_id") or "").strip()

    variables: dict[str, Any] = {}
    _put(variables, "channel.name", channel)
    _put(variables, "channel.is_voice", channel == "voice_sip")
    _put(variables, "channel.is_whatsapp", channel == "whatsapp")
    _put(variables, "channel.is_web_chat", channel == "web_chat")
    _put(variables, "channel.supports_audio", channel in {"voice_sip", "web_chat"})
    _put(variables, "session.id", session_id)
    _put(variables, "deploy.id", deploy_id)
    _put(variables, "org.id", org_id)
    _put(variables, "user.id", user_id)
    _put(variables, "user.phone", sender_phone)
    _put(variables, "caller_phone", sender_phone)

    for key, value in (memory_variables or {}).items():
        _put(variables, str(key), value)

    return variables


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    target[key] = value


def render_template(template: str, values: dict[str, Any] | None = None) -> str:
    """Replace {{var}} patterns with values. Supports dot-notation nesting."""
    values = values or {}

    def _lookup(key: str) -> Any:
        if key in values:
            return values.get(key)
        current: Any = values
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        value = _lookup(key)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return str(value)
        return str(value)

    return _VAR_RE.sub(_replace, template or "")
