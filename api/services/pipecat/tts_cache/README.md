# TTS cache

The cache supports MiniMax's independent streaming PCM requests. Other providers
and token aggregation bypass it. `MiniMaxCachingTTSService` owns provider
eligibility and request completion; `cached_synthesis` captures/replays audio and
enforces the audio-only output contract; `SpeechCache` owns validation, deadlines
and capture memory; `RedisCacheBackend` owns shared expiration and atomic
admission.

Provider adapters pass an opaque request payload plus an explicit PCM format to
`SynthesisRequest`. The shared cache does not interpret provider field names.
Adding an adapter requires independent requests, a verified completion signal,
and equivalent replay behavior.

`cached_synthesis` refuses any service whose yielded audio does not fully describe
its output, recording `unsupported_service` and synthesizing live. Word timestamps
are the reason this check cannot live in the capture loop: services push them
straight to the pipeline via `add_word_timestamps` instead of yielding them from
`run_tts`, so inspecting captured frames never observes them and a replayed entry
would drop them silently. The helper therefore reads `_push_text_frames` and
`_is_streaming_tokens` from the service itself rather than trusting each adapter
to re-check them, and treats a service reporting neither as unsafe.

Enable **Speech Caching** in the workflow's General settings. This persists
`workflow_configurations.tts_cache_enabled`, which defaults to false for existing
and new workflows. Save and publish through the usual workflow version flow.
Calls read their pinned definition; agent transfers read the destination's
resolved definition. Changing the setting does not require restarting workers.
An existing service retains its setting for the call.

Storage uses the application's existing `REDIS_URL` and a dedicated key prefix.
The binary client has its own bounded pool and short deadlines; it does not
change the shared Redis server's eviction policy. No cache environment variables
are required. Opted-in workflows in the same organization may reuse identical
requests; a disabled workflow neither reads nor populates the cache.

Resource limits are internal defaults in `CachePolicy`:

| Limit | Default | Purpose |
| --- | --- | --- |
| Expiration | 24 hours | Fixed expiration, not renewed on hits |
| Entry size | 1 MiB | PCM bytes per entry |
| Entry duration | 30 seconds | Audio duration per entry |
| Organization capacity | 128 entries | Atomic admission cap across workers |
| Capture memory | 64 MiB | In-flight PCM capture budget per worker |
| Operation timeout | 20 ms | Deadline for each Redis read/write |

The stricter byte/duration limit applies. Full organizations refuse new entries
until slots expire. The default caps audio at 128 MiB per organization using caching,
plus metadata; provision capacity for the number of participating organizations.
Capture accounting covers retained PCM buffers; transient serialization copies,
provider buffers and playback frames also consume memory. Cache errors fall back
to live synthesis, with a one-second local cooldown after storage failures.

Keys isolate organizations, endpoints/accounts, credential fingerprints, exact
prepared text, all effective request settings and PCM format. Values contain a
versioned JSON header and binary PCM with a checksum, never Python objects or
credentials. A hit creates fresh frames with the current context ID and emits no
provider character-usage event. The complete first synthesis is reused until
expiration; variation in pronunciation/emotion from repeated generation is lost.

To remove an organization's entries from an internal administrative task:

```python
from api.services.pipecat.tts_cache.runtime import get_speech_cache, close_speech_cache

cache = get_speech_cache(organization_id, enabled=True)
if cache is not None:
    removed = await cache.invalidate_organization(organization_id)
# Only standalone administrative tasks close the pool; the API lifespan owns it.
await close_speech_cache()
```

Invalidation removes entries present at that instant. In-flight completed
synthesis can repopulate them. For a strict cutover after changing a provider
voice under the same ID, prevent new calls using the old voice, drain active calls,
invalidate using a standalone task, then resume calls with the updated voice.
Backend invalidation errors are raised to its caller rather than reported as success.

When Prometheus is enabled, `dograh_tts_cache_*` instruments expose outcomes,
operation latency, admitted bytes, avoided characters and replay duration.
Traces receive `tts.cache` events. No phrase text or request key is a metric label.

Tests in `api/tests/test_tts_cache.py` use synthetic HTTP responses and uniquely
prefixed keys in the Redis selected by `api/.env.test`. MiniMax parser tests live
in `pipecat/tests/test_minimax_tts.py`. No provider credentials are needed. Test
real customer model/voice combinations before enabling production rollout.

From the repository root, run the focused tests with the test environment:

```bash
source venv/bin/activate
set -a
source api/.env.test
set +a
python -m pytest -c pipecat/pyproject.toml --asyncio-mode=auto \
  api/tests/test_tts_cache.py api/tests/test_minimax_service_factory.py \
  pipecat/tests/test_minimax_tts.py
```
