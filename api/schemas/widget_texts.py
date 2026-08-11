"""Visitor-facing copy for the embeddable widget.

This module is the single source of truth for the widget's default labels.
Nothing else may hardcode them:

- the dashboard reads the defaults from ``/user/configurations/defaults``,
  typed into the generated UI client, and shows them as input placeholders;
- ``dograh-widget.js`` receives the already-resolved copy from the public
  embed config endpoint, so the static asset carries no defaults at all.

Field names are snake_case in Python and camelCase on the wire, matching the
embed token ``settings`` keys the dashboard writes.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class WidgetTexts(BaseModel):
    """Every visitor-facing string the embed widget can render."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # Chat panel
    end_chat_text: str = "End chat"
    conversation_ended_text: str = "Conversation ended."
    start_new_chat_text: str = "Start new chat"
    chat_retry_text: str = "Retry"
    chat_input_placeholder: str = "Type a message…"
    send_message_label: str = "Send message"
    close_chat_label: str = "Close chat"

    # Voice call — button labels (floating CTA and inline button)
    voice_connecting_text: str = "Connecting…"
    voice_end_call_text: str = "End Call"
    voice_retry_text: str = "Retry"

    # Voice call — inline status headings and their subtexts
    voice_ready_title: str = "Ready to Connect"
    voice_connecting_subtext: str = "Please wait while we establish the connection"
    voice_connected_title: str = "Connected"
    voice_connected_subtext: str = "Your voice call is now active"
    voice_call_ended_title: str = "Call ended"
    voice_call_ended_subtext: str = "Click below to start a new call"
    voice_connection_failed_title: str = "Connection failed"
    voice_connection_failed_subtext: str = "Please check your microphone and try again"
    voice_connection_lost_title: str = "Connection lost"
    voice_connection_lost_subtext: str = "The call has been disconnected"

    @classmethod
    def resolve(cls, settings: dict | None) -> "WidgetTexts":
        """Apply the agent owner's overrides from embed token settings.

        Settings is a free-form blob that also carries non-text keys, and the
        dashboard omits a label the owner left blank, so unknown and blank
        entries both fall through to the default.
        """
        known_keys = {field.alias or name for name, field in cls.model_fields.items()}
        overrides = {
            key: value.strip()
            for key, value in (settings or {}).items()
            if key in known_keys and isinstance(value, str) and value.strip()
        }
        return cls(**overrides)
