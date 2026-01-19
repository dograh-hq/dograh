# STT Evaluation Benchmark

Benchmark for comparing Speech-to-Text providers using **WebSocket streaming** with focus on:
- **Speaker diarization** - identifying who said what
- **Keyterm boosting** - improving recognition of specific terms (Deepgram)

## Providers

| Provider | Diarization | Keyterm Boost | Streaming |
|----------|-------------|---------------|-----------|
| Deepgram | Yes | Yes | WebSocket (v1/v2) |
| Speechmatics | Yes | Additional vocab | WebSocket RT |

## Setup

```bash
# Install dependencies
pip install websockets

# Set API keys
export DEEPGRAM_API_KEY="your-key"
export SPEECHMATICS_API_KEY="your-key"
```

**Note:** Requires `ffmpeg` installed for audio conversion to PCM16.

## Usage

Run from the project root directory:

```bash
# Test both providers with diarization
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize

# Test only Deepgram
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --providers deepgram

# Test with keyterm boosting (Deepgram)
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --keyterms "Dograh" "Pipecat"

# Use different sample rate (default: 8000 Hz)
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --sample-rate 16000

# Show word-level timings
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --show-words

# Save results to JSON
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --save
```

## CLI Options

| Option | Description |
|--------|-------------|
| `audio_file` | Path to audio file (relative to evals/stt/ or absolute) |
| `--providers` | Providers to test: `deepgram`, `speechmatics` (default: both) |
| `--diarize` | Enable speaker diarization |
| `--keyterms` | Keywords to boost (Deepgram) / additional vocab (Speechmatics) |
| `--language` | Language code (default: en) |
| `--sample-rate` | Audio sample rate for streaming (default: 8000) |
| `--show-words` | Show individual word timings |
| `--save` | Save results to JSON in `results/` |

## Directory Structure

```
evals/stt/
├── audio/              # Audio test files
│   └── multi_speaker.m4a
├── results/            # Saved benchmark results (JSON)
├── providers/          # STT provider implementations
│   ├── base.py         # Base classes
│   ├── deepgram_provider.py    # WebSocket streaming
│   └── speechmatics_provider.py # WebSocket streaming
├── audio_streamer.py   # PCM16 audio file streamer
├── benchmark.py        # Main runner script
└── README.md
```

## How It Works

1. **Audio Conversion**: The `AudioStreamer` converts any audio file to raw PCM16 using ffmpeg
2. **WebSocket Connection**: Providers connect to their respective WebSocket APIs
3. **Streaming**: Audio is sent in chunks (configurable sample rate, default 8kHz)
4. **Result Collection**: Transcripts and speaker info are collected from WebSocket responses
5. **Comparison**: Results are parsed into a common format for comparison

## Output Example

```
Audio file: /path/to/audio/multi_speaker.m4a
Providers: ['deepgram', 'speechmatics']
Diarization: True
Sample rate: 8000 Hz

============================================================
Provider: DEEPGRAM
============================================================

Duration: 45.32s
Speakers detected: 2 - ['0', '1']

Transcript:
Hello, welcome to the demo...

--- Speaker Segments ---
[0.0s] Speaker 0: Hello, welcome to the demo.
[2.5s] Speaker 1: Thanks for having me.
...

============================================================
COMPARISON SUMMARY
============================================================

Provider        Duration   Speakers   Words
---------------------------------------------
deepgram        45.32      2          312
speechmatics    45.32      2          308
```

## Adding New Providers

1. Create a new file in `providers/` (e.g., `whisper_provider.py`)
2. Implement the `STTProvider` abstract class with WebSocket streaming
3. Use `AudioStreamer` for PCM16 conversion
4. Add to `providers/__init__.py`
5. Add to `benchmark.py` provider choices

## API Documentation

- Deepgram Streaming: https://developers.deepgram.com/docs/live-streaming-audio
- Deepgram Diarization: https://developers.deepgram.com/docs/diarization
- Deepgram Keyterms: https://developers.deepgram.com/docs/keyterm
- Speechmatics RT API: https://docs.speechmatics.com/rt-api-ref
- Speechmatics Diarization: https://docs.speechmatics.com/features/diarization
