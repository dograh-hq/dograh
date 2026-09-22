# TTS cache admission

For each organization and exact synthesis request, the first three requests
reserve candidate slots in Redis **before** invoking the TTS engine. When all
three reserved generations finish successfully, Redis
atomically selects the candidate with the median **complete PCM duration**.
Subsequent requests replay that recording unchanged, beginning with the fourth
sequential request. Duration includes all engine-generated silence and pauses;
there is no trimming, stretching, phrase segmentation, or acoustic model.

The existing request digest includes the effective provider payload, credentials,
endpoint, adapter version, and output audio format. Samples from different texts
or settings are never pooled. Selection requires no global pace baseline or
separate counter: completed candidates plus active reservations occupy three
slots. Equal durations use completion order to break ties; independently generated
identical audio still counts.

Only completed, supported PCM streams within the existing capture and entry
limits count. Errors, interruptions, and incomplete synthesis release their
reservation without adding audio, allowing a later request to contribute.
These checks do not establish transcript accuracy or guarantee that
three samples represent every future generation.

Claims, submissions, and releases are atomic Redis operations shared across
workers. Each claim has a unique ownership token and a 120-second deadline.
Expired claims become available to later requests; expired owners cannot submit
audio or release a replacement's reservation. This deadline limits warm-up stalls
when a worker crashes; a generation exceeding it still plays live but is excluded
from caching. The deadline does not renew when audio chunks arrive.

Requests arriving while all slots are occupied still stream live, without
capturing a cache candidate. Their recordings cannot enter the pool even if they
finish before the reserved requests. Already-running syntheses always finish
playing their own live audio. No extra background generations are started.

Warming pools and selected entries share the organization LRU limit and idle TTL.
A warming pool retains at most two recordings between operations, so its audio
storage can be twice that of a selected entry. After selection, the other recordings
are discarded. Metadata retains the three durations, selected completion number, and
selection policy for inspection. Management lists and previews show selected
entries; deletion and organization clearing also remove warming pools and leases.
Eviction, deletion, clearing, and malformed-pool recovery invalidate outstanding
tokens, so old workers cannot repopulate a pool with late completions.

The default Redis namespace is `dograh:tts:v3`. Older namespaces allowed writers
without reservations (`v1` froze the first take; `v2` selected the first three
completions). They expire normally after old workers stop accessing them. The
separate namespace prevents old workers from bypassing reservations during a
rolling deployment. This change starts a fresh warm-up period; no production
cache migration or deletion is required.
