"""Provider-specific voice catalogs. Credentials are supplied by the saved connection."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import urlparse, urlunparse

import httpx

from api.schemas.voices import VoiceInfo
from api.services.configuration.options.deepgram import DEEPGRAM_DEFAULT_BASE_URL
from api.services.configuration.options.sarvam import (
    SARVAM_V3_VOICES,
    SARVAM_V4_VOICES,
)


@dataclass(frozen=True)
class VoiceQuery:
    model: str | None = None
    language: str | None = None
    q: str | None = None
    gender: str | None = None
    accent: str | None = None


def _catalog_url(base_url: str, path: str) -> str:
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    scheme = {"wss": "https", "ws": "http"}.get(parsed.scheme, parsed.scheme)
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid connection base URL")
    return urlunparse(
        (scheme, parsed.netloc, f"{parsed.path.rstrip('/')}{path}", "", "", "")
    )


def _next_cursor(value: Any, seen: set[str]) -> str:
    if not isinstance(value, str) or not value or value in seen:
        raise ValueError("Invalid voice catalog pagination")
    seen.add(value)
    return value


class VoiceCatalog(ABC):
    name: str
    base_url = ""
    path = ""
    auth_header: str | None = None
    auth_prefix = ""
    server_filters: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, api_key: str | None, base_url: str | None):
        self.url = (
            _catalog_url(base_url or self.base_url, self.path) if self.path else None
        )
        self.headers = (
            {self.auth_header: f"{self.auth_prefix}{api_key}"}
            if self.auth_header and api_key
            else {}
        )

    async def get(
        self, client: httpx.AsyncClient, params=None, url: str | None = None
    ) -> Any:
        response = await client.get(
            url or self.url, headers=self.headers, params=params
        )
        response.raise_for_status()
        return response.json()

    @abstractmethod
    async def voices(
        self, client: httpx.AsyncClient, query: VoiceQuery
    ) -> list[VoiceInfo]:
        """Fetch voices, applying model/language filters supported by this provider."""


class ElevenLabsVoiceCatalog(VoiceCatalog):
    name = "ElevenLabs"
    base_url = "https://api.elevenlabs.io"
    path = "/v2/voices"
    auth_header = "xi-api-key"
    server_filters = frozenset({"q", "gender", "accent"})

    async def voices(self, client, query):
        # https://elevenlabs.io/docs/api-reference/voices/search
        params = {"page_size": 100, "include_total_count": "false"}
        for name, value in (
            ("search", query.q),
            ("language", query.language),
            ("gender", query.gender),
            ("accent", query.accent),
        ):
            if value:
                params[name] = value
        voices = []
        seen = set()
        while True:
            page = await self.get(client, params)
            for entry in page["voices"]:
                voice = self._voice_for_model(entry, query)
                if voice is not None:
                    voices.append(voice)
            if not page.get("has_more"):
                return voices
            params["next_page_token"] = _next_cursor(page.get("next_page_token"), seen)

    @staticmethod
    def _voice_for_model(voice: dict[str, Any], query: VoiceQuery) -> VoiceInfo | None:
        # The API has no model filter. High-quality model IDs and verified
        # languages are not exhaustive compatibility allow-lists.
        states = (voice.get("fine_tuning") or {}).get("state") or {}
        if (
            query.model
            and query.model in states
            and states[query.model] != "fine_tuned"
        ):
            return None
        labels = voice.get("labels") or {}
        previews = [
            entry
            for entry in voice.get("verified_languages") or []
            if (not query.model or entry.get("model_id") == query.model)
            and (not query.language or entry.get("language") == query.language)
        ]
        preview = previews[0] if previews else {}
        return VoiceInfo(
            voice_id=voice["voice_id"],
            name=voice["name"],
            description=voice.get("description"),
            accent=preview.get("accent") or labels.get("accent"),
            gender=labels.get("gender"),
            language=preview.get("language") or labels.get("language"),
            preview_url=preview.get("preview_url") or voice.get("preview_url"),
        )


class CartesiaVoiceCatalog(VoiceCatalog):
    name = "Cartesia"
    base_url = "https://api.cartesia.ai"
    path = "/voices"
    auth_header = "Authorization"
    auth_prefix = "Bearer "
    server_filters = frozenset({"q", "gender"})

    async def voices(self, client, query):
        # https://docs.cartesia.ai/api-reference/voices/list
        # Catalog voices work across Sonic models; the API has no model filter.
        self.headers["Cartesia-Version"] = "2026-08-14"
        params = {"limit": 100, "expand[]": "preview_file_url"}
        genders = {
            "female": "feminine",
            "male": "masculine",
            "neutral": "gender_neutral",
        }
        for name, value in (
            ("q", query.q),
            ("language", query.language),
            ("gender", genders.get(query.gender, query.gender)),
        ):
            if value:
                params[name] = value
        voices = []
        seen = set()
        while True:
            page = await self.get(client, params)
            for entry in page["data"]:
                accents = entry.get("accents") or []
                native = next((item for item in accents if item.get("is_native")), {})
                voices.append(
                    VoiceInfo(
                        voice_id=entry["id"],
                        name=entry["name"],
                        description=entry.get("description"),
                        gender={v: k for k, v in genders.items()}.get(
                            entry.get("gender"), entry.get("gender")
                        ),
                        language=entry.get("language"),
                        accent=native.get("accent"),
                        preview_url=entry.get("preview_file_url"),
                    )
                )
            if not page.get("has_more"):
                return voices
            params["starting_after"] = _next_cursor(page.get("next_page"), seen)


class DeepgramVoiceCatalog(VoiceCatalog):
    name = "Deepgram"
    base_url = DEEPGRAM_DEFAULT_BASE_URL
    path = "/v1/models"
    auth_header = "Authorization"
    auth_prefix = "Token "
    # Aura voices are listed by /v1/models and Flux voices by /v2/models, and
    # neither listing includes the other family. Each listing's architecture
    # names are mapped onto the configuration's model values.
    listings: ClassVar[tuple[tuple[str, dict[str, str]], ...]] = (
        ("/v1/models", {"aura": "aura-1", "aura-2": "aura-2"}),
        ("/v2/models", {"flux-tts": "flux"}),
    )

    def __init__(self, api_key: str | None, base_url: str | None):
        super().__init__(api_key, base_url)
        self.listing_urls = [
            (_catalog_url(base_url or self.base_url, path), models)
            for path, models in self.listings
        ]

    async def voices(self, client, query):
        # https://developers.deepgram.com/guides/fundamentals/model-metadata
        voices = []
        for url, models in self.listing_urls:
            if query.model and query.model not in models.values():
                continue
            data = await self.get(client, url=url)
            for entry in data["tts"]:
                voice_id = entry["canonical_name"]
                architecture = entry.get("architecture")
                if not architecture:
                    architecture = (
                        "aura-2" if voice_id.startswith("aura-2-") else "aura"
                    )
                model = models.get(architecture, architecture)
                if query.model and model != query.model:
                    continue
                languages = entry.get("languages") or []
                if query.language and query.language not in languages:
                    continue
                metadata = entry.get("metadata") or {}
                tags = metadata.get("tags") or []
                voices.append(
                    VoiceInfo(
                        voice_id=voice_id,
                        name=metadata.get("display_name") or entry["name"].title(),
                        description=", ".join(tags) or None,
                        accent=metadata.get("accent"),
                        gender=next(
                            (
                                {"feminine": "female", "masculine": "male"}[tag]
                                for tag in tags
                                if tag in {"feminine", "masculine"}
                            ),
                            None,
                        ),
                        language=query.language or next(iter(languages), None),
                        preview_url=metadata.get("sample"),
                    )
                )
        return voices


class RimeVoiceCatalog(VoiceCatalog):
    name = "Rime"
    base_url = "https://users.rime.ai"
    path = "/data/voices/all-v2.json"
    # Rime's catalog is public. Do not send the connection's secret to it.
    # https://docs.rime.ai/api-reference/data/voices-v2
    languages: ClassVar[dict[str, str]] = {
        "eng": "en",
        "spa": "es",
        "fra": "fr",
        "fre": "fr",
        "ger": "de",
        "deu": "de",
        "hin": "hi",
        "por": "pt",
        "jpn": "ja",
        "ara": "ar",
        "ita": "it",
    }

    async def voices(self, client, query):
        data = await self.get(client)
        voices = []
        for model, languages in data.items():
            if query.model and model != query.model:
                continue
            for language, names in languages.items():
                language = self.languages.get(language, language)
                if query.language and language != self.languages.get(
                    query.language, query.language
                ):
                    continue
                voices.extend(
                    VoiceInfo(
                        voice_id=name,
                        name=name.replace("_", " ").title(),
                        language=language,
                    )
                    for name in names
                )
        return voices


SARVAM_LANG_NAMES: dict[str, tuple[str, str]] = {
    "as": ("Assamese", "as"),
    "bn": ("Bengali", "bn"),
    "en": ("English", "en"),
    "enhi": ("English-Hindi", "en-hi"),
    "gu": ("Gujarati", "gu"),
    "hi": ("Hindi", "hi"),
    "kn": ("Kannada", "kn"),
    "mr": ("Marathi", "mr"),
    "pa": ("Punjabi", "pa"),
    "ta": ("Tamil", "ta"),
    "te": ("Telugu", "te"),
}


def _sarvam_style_name(style_key: str) -> str:
    if style_key == "kyc":
        return "KYC"
    return style_key.replace("_", " ").title()


class SarvamVoiceCatalog(VoiceCatalog):
    name = "Sarvam"

    async def voices(self, client, query):
        # Bulbul v3/v4 speakers are static. The separate voice-cloning API uses
        # a different synthesis endpoint that our current service doesn't use.
        # https://docs.sarvam.ai/api/migrations/from-cartesia/text-to-speech
        options = {
            "bulbul:v4-flash": SARVAM_V4_VOICES,
            "bulbul:v3": SARVAM_V3_VOICES,
        }
        names = options.get(query.model or "bulbul:v4-flash", ())
        if query.model == "bulbul:v3":
            return [
                VoiceInfo(
                    voice_id=name,
                    name=name.title(),
                    description="Multilingual",
                    language="multilingual",
                )
                for name in names
            ]
        voices = []
        for voice_id in names:
            parts = voice_id.split("_", 2)
            if len(parts) == 3:
                raw_name, lang_code, style_key = parts
                lang_name, lang_tag = SARVAM_LANG_NAMES.get(
                    lang_code, (lang_code.title(), lang_code)
                )
                style_name = _sarvam_style_name(style_key)
                voices.append(
                    VoiceInfo(
                        voice_id=voice_id,
                        name=raw_name.title(),
                        description=f"{lang_name} • {style_name} • {voice_id}",
                        language=lang_tag,
                    )
                )
            else:
                voices.append(
                    VoiceInfo(
                        voice_id=voice_id,
                        name=voice_id.replace("_", " ").title(),
                    )
                )
        return voices


VOICE_CATALOGS: dict[str, type[VoiceCatalog]] = {
    "elevenlabs": ElevenLabsVoiceCatalog,
    "cartesia": CartesiaVoiceCatalog,
    "deepgram": DeepgramVoiceCatalog,
    "rime": RimeVoiceCatalog,
    "sarvam": SarvamVoiceCatalog,
}


def create_voice_catalog(
    provider: str, *, api_key: str | None, base_url: str | None
) -> VoiceCatalog:
    return VOICE_CATALOGS[provider](api_key=api_key, base_url=base_url)
