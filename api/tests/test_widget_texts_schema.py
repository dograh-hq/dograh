from api.schemas.widget_texts import WidgetTexts


def test_defaults_serialize_with_camel_case_settings_keys():
    """The widget and the dashboard both address these by settings key, so the
    wire names must stay camelCase even though the fields are snake_case."""
    payload = WidgetTexts().model_dump(by_alias=True)

    assert payload["endChatText"] == "End chat"
    assert payload["voiceReadyTitle"] == "Ready to Connect"
    assert not [key for key in payload if "_" in key]


def test_resolve_applies_overrides_and_trims():
    resolved = WidgetTexts.resolve(
        {"endChatText": "  Finalizar chat  ", "voiceEndCallText": "Colgar"}
    )

    assert resolved.end_chat_text == "Finalizar chat"
    assert resolved.voice_end_call_text == "Colgar"


def test_resolve_falls_back_for_blank_unknown_and_non_string_entries():
    """Settings is a shared free-form blob: it carries widget config that is not
    copy at all, and the dashboard drops labels the owner cleared."""
    resolved = WidgetTexts.resolve(
        {
            "voiceReadyTitle": "   ",
            "sendMessageLabel": None,
            "buttonText": "Talk to Agent",
            "autoStart": False,
        }
    )

    assert resolved.voice_ready_title == "Ready to Connect"
    assert resolved.send_message_label == "Send message"


def test_resolve_tolerates_missing_settings():
    assert WidgetTexts.resolve(None) == WidgetTexts()
