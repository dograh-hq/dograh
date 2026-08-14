"""
Conftest for the ARI provider outbound tests.

Pre-existing environment issue: the Dograh code targets an older pipecat
(0.x) where pipecat.utils.run_context, pipecat.audio.mixers.*, and
pipecat.audio.dtmf all existed. The venv has pipecat-ai 1.7.0 which
renamed/removed these. The full test conftest (api/conftest.py) fails to
import because transport.py triggers the whole pipecat import chain.

This conftest stubs the missing pipecat submodules so that
test_provider_outbound.py can be collected and run by pytest against
the REAL ARIProvider._normalize_sip_endpoint method.
"""
import sys
import types
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost")
os.environ.setdefault("ENVIRONMENT", "test")


def _stub_pipecat():
    """Lazily stub pipecat submodules missing in 1.7.0."""
    import pipecat
    import pipecat.utils

    # --- pipecat.utils.run_context ---
    # pipecat 1.7.0 has this module but removed set_current_org_id /
    # clear_current_org_id / RunContext that Dograh imports. Patch the
    # missing symbols onto the existing module (or create a stub module
    # when the module itself is absent).
    rc_fullname = "pipecat.utils.run_context"
    if rc_fullname not in sys.modules:
        m = types.ModuleType(rc_fullname)
        m.get_current_org_id = lambda: 1
        m.RunContext = None
        m.set_current_org_id = lambda *a, **kw: None
        m.clear_current_org_id = lambda *a, **kw: None
        pipecat.utils.run_context = m
        sys.modules[rc_fullname] = m
    else:
        m = sys.modules[rc_fullname]
        m.get_current_org_id = getattr(m, "get_current_org_id", lambda: 1)
        m.RunContext = getattr(m, "RunContext", None)
        m.set_current_org_id = getattr(m, "set_current_org_id", lambda *a, **kw: None)
        m.clear_current_org_id = getattr(m, "clear_current_org_id", lambda *a, **kw: None)

    # --- pipecat.audio package ---
    if not hasattr(pipecat, "audio") or not isinstance(getattr(pipecat, "audio", None), types.ModuleType):
        pipecat.audio = types.ModuleType("pipecat.audio")
        pipecat.audio.__path__ = []
        sys.modules["pipecat.audio"] = pipecat.audio
    pa = pipecat.audio

    # --- pipecat.audio.mixers ---
    if not hasattr(pa, "mixers") or not isinstance(getattr(pa, "mixers", None), types.ModuleType):
        mixers = types.ModuleType("pipecat.audio.mixers")
        mixers.__path__ = []
        pa.mixers = mixers
        sys.modules["pipecat.audio.mixers"] = mixers

    # silence_mixer
    if "pipecat.audio.mixers.silence_mixer" not in sys.modules:
        sm = types.ModuleType("pipecat.audio.mixers.silence_mixer")
        sm.SilenceAudioMixer = type("SilenceAudioMixer", (), {})
        pa.mixers.silence_mixer = sm
        sys.modules["pipecat.audio.mixers.silence_mixer"] = sm

    # base_audio_mixer
    if "pipecat.audio.mixers.base_audio_mixer" not in sys.modules:
        bm = types.ModuleType("pipecat.audio.mixers.base_audio_mixer")
        bm.BaseAudioMixer = type("BaseAudioMixer", (), {})
        pa.mixers.base_audio_mixer = bm
        sys.modules["pipecat.audio.mixers.base_audio_mixer"] = bm

    # soundfile_mixer — pipecat 1.7.0 renamed SoundfileMixer ->
    # SoundfileAudioMixer. Dograh's audio_mixer.py still imports the old
    # name. Alias the old name onto the real (renamed) class when the module
    # is importable, or provide a stub type when it isn't. This is a test-only
    # isolation shim; production audio_mixer.py is left untouched.
    sf_fullname = "pipecat.audio.mixers.soundfile_mixer"
    try:
        import pipecat.audio.mixers.soundfile_mixer as _sf
        if not hasattr(_sf, "SoundfileMixer"):
            _sf.SoundfileMixer = getattr(
                _sf, "SoundfileAudioMixer", type("SoundfileMixer", (), {})
            )
    except ImportError:
        stub = types.ModuleType(sf_fullname)
        stub.SoundfileMixer = type("SoundfileMixer", (), {})
        pa.mixers.soundfile_mixer = stub
        sys.modules[sf_fullname] = stub

    # --- pipecat.audio.dtmf ---
    if not hasattr(pa, "dtmf"):
        dtmf = types.ModuleType("pipecat.audio.dtmf")
        dtmf.KeypadEntry = type("KeypadEntry", (), {})
        pa.dtmf = dtmf
        sys.modules["pipecat.audio.dtmf"] = dtmf

    # --- pipecat.transports.websocket.fastapi (stub entirely) ---
    # This is where transport.py imports from. We provide a minimal stub
    # so that create_transport's import doesn't fail, but we don't need
    # the actual implementation for testing _normalize_sip_endpoint.
    if "pipecat.transports.websocket.fastapi" not in sys.modules:
        fapi = types.ModuleType("pipecat.transports.websocket.fastapi")

        class FastAPIWebsocketTransport:
            """Stub — not used by _normalize_sip_endpoint tests."""
            def __init__(self, *args, **kwargs):
                pass

        fapi.FastAPIWebsocketTransport = FastAPIWebsocketTransport
        sys.modules["pipecat.transports.websocket.fastapi"] = fapi

    # --- pipecat.serializers.<name> ---
    # pipecat 1.7.0 removed the per-provider serializer modules
    # (asterisk, twilio, plivo, telnyx, vonage, cloudonix, etc.) that
    # each provider's serializers.py imports. Stub each one on demand so
    # the telephony provider import-for-registration chain can complete.
    import pipecat.serializers  # noqa: E402  — package exists in 1.7.0
    _serializer_stubs = {
        "asterisk": ["AsteriskFrameSerializer"],
        "cloudonix": ["CloudonixFrameSerializer"],
        "vobiz": ["VobizFrameSerializer"],
        "twilio": ["TwilioFrameSerializer"],
        "plivo": ["PlivoFrameSerializer"],
        "telnyx": ["TelnyxFrameSerializer"],
        "vonage": ["VonageFrameSerializer"],
        "call_strategies": ["HangupStrategy", "TransferStrategy"],
    }
    for modname, cls_names in _serializer_stubs.items():
        fullname = f"pipecat.serializers.{modname}"
        if fullname not in sys.modules:
            stub = types.ModuleType(fullname)
            for cls_name in cls_names:
                setattr(stub, cls_name, type(cls_name, (), {}))
            sys.modules[fullname] = stub


_stub_pipecat()
