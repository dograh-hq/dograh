from api.services.configuration.registry import SarvamTTSConfiguration

# Languages Sarvam's /text-to-speech accepts: 10 Indian languages plus English,
# per https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert (the same
# 11 codes as sarvamai's TextToSpeechLanguage).
SARVAM_TTS_SUPPORTED = {
    "bn-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
}


def test_sarvam_tts_offers_only_languages_sarvam_can_speak():
    # service_factory maps an unknown code to Language.HI, so offering a code
    # Sarvam TTS lacks makes the agent speak Hindi without any error.
    offered = SarvamTTSConfiguration.model_json_schema()["properties"]["language"][
        "examples"
    ]
    assert offered and set(offered) <= SARVAM_TTS_SUPPORTED
