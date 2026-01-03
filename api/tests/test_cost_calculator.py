from api.services.pricing.cost_calculator import cost_calculator


def test_cost_calculator():
    """Test function to verify cost calculation works"""
    sample_usage = {
        "llm": {
            "OpenAILLMService#0|||gpt-4.1-mini": {
                "prompt_tokens": 45380,
                "completion_tokens": 496,
                "total_tokens": 45876,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            }
        },
        "tts": {"ElevenLabsTTSService#0|||eleven_flash_v2_5": 2399},
        "stt": {"DeepgramSTTService#0|||nova-3-general": 177.21536946296692},
        "call_duration_seconds": 179,
    }

    result = cost_calculator.calculate_total_cost(sample_usage)
    assert result["llm_cost"] == 45380 * 0.40 / 1_000_000 + 496 * 1.60 / 1_000_000
    assert result["tts_cost"] == 2399 * 0.0256 / 1_000
    assert result["stt_cost"] == 177.21536946296692 / 60 * 0.0077
    assert (
        abs(
            result["total"]
            - (result["llm_cost"] + result["tts_cost"] + result["stt_cost"])
        )
        < 1e-10
    )
