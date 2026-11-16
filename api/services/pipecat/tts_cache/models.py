"""Tenant-scoped request identities and a versioned PCM cache format."""

import hashlib
import json
import math
import struct
from dataclasses import dataclass

# A version 1 header is a few fixed fields. Version 2 adds the words spoken in
# the audio, so its size grows with the sentence.
MAX_HEADER_BYTES = 32 * 1024

# Word timestamps come from the provider's subtitles, which can end slightly
# after the last audio sample.
_WORD_END_TOLERANCE_SECONDS = 0.25


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

    @property
    def max_value_bytes(self) -> int:
        """Largest encoded entry: the header length prefix, header and audio."""
        return self.max_entry_bytes + 4 + MAX_HEADER_BYTES


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
    # The words spoken in the audio, each with its start and end in seconds from
    # the first sample. Empty for providers that report no word timestamps.
    words: tuple[tuple[str, float, float], ...] = ()

    @property
    def duration_seconds(self) -> float:
        return len(self.audio) / (self.sample_rate * self.channels * 2)

    def validate(self, request: SynthesisRequest, policy: CachePolicy) -> None:
        if (
            self.sample_rate != request.sample_rate
            or self.channels != request.channels
            or not self.audio
            or len(self.audio) % (2 * self.channels)
            or len(self.audio) > policy.audio_limit(self.sample_rate, self.channels)
        ):
            raise ValueError("Invalid cached PCM")
        latest_end = self.duration_seconds + _WORD_END_TOLERANCE_SECONDS
        for word in self.words:
            if (
                type(word) is not tuple
                or len(word) != 3
                or type(word[0]) is not str
                or not word[0]
                or type(word[1]) not in (int, float)
                or type(word[2]) not in (int, float)
                or not 0 <= word[1] <= word[2] <= latest_end
            ):
                raise ValueError("Invalid cached word timestamps")

    def encode(self) -> bytes:
        header = {
            "version": 2 if self.words else 1,
            "encoding": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "length": len(self.audio),
            "sha256": hashlib.sha256(self.audio).hexdigest(),
        }
        if self.words:
            # Stored exactly as validated: rounding could move an end past the
            # limit validate() allowed, and decode would then reject the entry.
            header["words"] = [[word, start, end] for word, start, end in self.words]
        encoded = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_HEADER_BYTES:
            raise ValueError("Cached speech header too large")
        return struct.pack("!I", len(encoded)) + encoded + self.audio

    @classmethod
    def decode(
        cls, value: bytes, request: SynthesisRequest, policy: CachePolicy
    ) -> "CachedSpeech":
        if len(value) < 4 or len(value) > policy.max_value_bytes:
            raise ValueError("Invalid cached speech size")
        size = struct.unpack("!I", value[:4])[0]
        if size > MAX_HEADER_BYTES:
            raise ValueError("Invalid cached speech header")
        header = json.loads(value[4 : 4 + size])
        audio = value[4 + size :]
        if (
            header["version"] not in (1, 2)
            or header["encoding"] != "pcm_s16le"
            or header["length"] != len(audio)
            or header["sha256"] != hashlib.sha256(audio).hexdigest()
            or type(header["sample_rate"]) is not int
            or type(header["channels"]) is not int
        ):
            raise ValueError("Invalid cached speech metadata")
        words = ()
        if header["version"] == 2:
            raw_words = header["words"]
            if type(raw_words) is not list or not raw_words:
                raise ValueError("Invalid cached word timestamps")
            if any(type(word) is not list or len(word) != 3 for word in raw_words):
                raise ValueError("Invalid cached word timestamps")
            words = tuple(tuple(word) for word in raw_words)
        speech = cls(audio, header["sample_rate"], header["channels"], words)
        speech.validate(request, policy)
        return speech
