from api.services.workflow.run_usage_response import format_public_usage_info


def test_format_public_usage_info():
    usage_info = {
        "llm": {
            "SarvamLLMService#0|||sarvam-30b": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        },
        "tts": {"ElevenLabsTTSService#0|||eleven_flash_v2_5": 42},
        "stt": {},
        "call_duration_seconds": 12.4,
    }

    result = format_public_usage_info(usage_info)

    assert result["llm"]["SarvamLLMService#0|||sarvam-30b"]["prompt_tokens"] == 100
    assert result["tts"]["ElevenLabsTTSService#0|||eleven_flash_v2_5"] == 42
    assert result["stt"] == {}
    assert result["call_duration_seconds"] == 12.4
