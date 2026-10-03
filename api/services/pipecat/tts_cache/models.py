"""Tenant-scoped request identities and a versioned PCM cache format."""

import hashlib
import json
import math
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int = 86400
    max_entry_bytes: int = 1024 * 1024
    max_duration_seconds: float = 30
    max_entries_per_org: int = 512
    max_evictions_per_put: int = 64
    operation_timeout_seconds: float = 0.02
    max_capture_bytes: int = 64 * 1024 * 1024
    failure_cooldown_seconds: float = 1
    reservation_ttl_seconds: float = 120

    def __post_init__(self):
        for value in vars(self).values():
            if not math.isfinite(value) or value <= 0:
                raise ValueError("TTS cache limits must be finite and positive")

    def audio_limit(self, sample_rate: int, channels: int) -> int:
        return min(
            self.max_entry_bytes,
            int(self.max_duration_seconds * sample_rate * channels * 2),
        )


@dataclass(frozen=True)
class SynthesisRequest:
    organization_id: int
    provider: str
    digest: str
    sample_rate: int
    channels: int
    text_preview: str = ""
    model: str = ""
    voice_id: str = ""

    @classmethod
    def from_payload(
        cls,
        *,
        organization_id: int,
        provider: str,
        adapter_version: int,
        endpoint: str,
        credential: str,
        payload: dict,
        sample_rate: int,
        channels: int,
        encoding: str = "pcm_s16le",
    ) -> "SynthesisRequest":
        """Hash an opaque provider payload and the adapter's output audio contract."""
        if type(organization_id) is not int or organization_id <= 0:
            raise ValueError("TTS caching requires an organization")
        if (
            encoding != "pcm_s16le"
            or type(sample_rate) is not int
            or not 0 < sample_rate <= 192000
            or type(channels) is not int
            or channels != 1
        ):
            raise ValueError("Unsupported TTS cache audio format")
        identity = {
            "provider": provider,
            "adapter_version": adapter_version,
            "endpoint": endpoint,
            "credential": hashlib.sha256(credential.encode()).hexdigest(),
            "payload": payload,
            "output": {
                "encoding": encoding,
                "sample_rate": sample_rate,
                "channels": channels,
            },
        }
        canonical = json.dumps(
            identity, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return cls(
            organization_id,
            provider,
            hashlib.sha256(canonical.encode()).hexdigest(),
            sample_rate,
            channels,
            str(payload.get("text", ""))[:1000],
            str(payload.get("model", ""))[:200],
            str((payload.get("voice_setting") or {}).get("voice_id", ""))[:200],
        )


@dataclass(frozen=True)
class CachedSpeech:
    audio: bytes
    sample_rate: int
    channels: int = 1

    def validate(self, request: SynthesisRequest, policy: CachePolicy) -> None:
        if (
            self.sample_rate != request.sample_rate
            or self.channels != request.channels
            or not self.audio
            or len(self.audio) % (2 * self.channels)
            or len(self.audio) > policy.audio_limit(self.sample_rate, self.channels)
        ):
            raise ValueError("Invalid cached PCM")

    def encode(self) -> bytes:
        header = json.dumps(
            {
                "version": 1,
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "length": len(self.audio),
                "sha256": hashlib.sha256(self.audio).hexdigest(),
            },
            separators=(",", ":"),
        ).encode()
        return struct.pack("!I", len(header)) + header + self.audio

    @classmethod
    def decode(
        cls, value: bytes, request: SynthesisRequest, policy: CachePolicy
    ) -> "CachedSpeech":
        if len(value) < 4 or len(value) > policy.max_entry_bytes + 1028:
            raise ValueError("Invalid cached speech size")
        size = struct.unpack("!I", value[:4])[0]
        if size > 1024:
            raise ValueError("Invalid cached speech header")
        header = json.loads(value[4 : 4 + size])
        audio = value[4 + size :]
        if (
            header["version"] != 1
            or header["encoding"] != "pcm_s16le"
            or header["length"] != len(audio)
            or header["sha256"] != hashlib.sha256(audio).hexdigest()
            or type(header["sample_rate"]) is not int
            or type(header["channels"]) is not int
        ):
            raise ValueError("Invalid cached speech metadata")
        speech = cls(audio, header["sample_rate"], header["channels"])
        speech.validate(request, policy)
        return speech
