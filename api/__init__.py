# Ensure SpeechBoundaryFrame compatibility across pipecat versions
try:
    from pipecat.frames.frames import SpeechBoundaryFrame
except (ImportError, AttributeError):
    try:
        from dataclasses import dataclass
        import pipecat.frames.frames
        from pipecat.frames.frames import ControlFrame

        @dataclass
        class SpeechBoundaryFrame(ControlFrame):
            speech_id: str
            beginning: bool

        pipecat.frames.frames.SpeechBoundaryFrame = SpeechBoundaryFrame
    except Exception:
        pass
