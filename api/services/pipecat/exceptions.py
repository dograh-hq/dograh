"""Custom exceptions raised by the pipecat-runtime layer."""


class VoicemailDetectedException(Exception):
    """
    Exception raised when voicemail is detected.
    """

    pass


class UnsupportedRealtimeFeatureError(Exception):
    """
    Raised at pipeline runtime when a workflow attempts to use a feature
    the configured realtime provider does not support (e.g. function calling
    against a translation-only model).
    """

    pass
