# STT Evaluation Benchmark

Benchmark for comparing Speech-to-Text providers with focus on:
- **Speaker diarization** - identifying who said what
- **Keyterm boosting** - improving recognition of specific terms (Deepgram)

## Providers

| Provider | Diarization | Keyterm Boost | Notes |
|----------|-------------|---------------|-------|
| Deepgram | Yes | Yes | `diarize=true`, `keyterm` param |
| Speechmatics | Yes | No | `diarization: "speaker"` config |

## Setup

```bash
# Install dependencies (httpx is required)
pip install httpx

# Set API keys
export DEEPGRAM_API_KEY="your-key"
export SPEECHMATICS_API_KEY="your-key"
```

## Usage

Run from the project root directory:

```bash
# Test both providers with diarization
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize

# Test only Deepgram
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --providers deepgram

# Test with keyterm boosting (Deepgram only)
python -m evals.stt.benchmark audio/multi_speaker.m4a --diarize --keyterms "Dograh" "Pipecat"

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
| `--keyterms` | Keywords to boost (Deepgram only) |
| `--language` | Language code (default: en) |
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
│   ├── deepgram_provider.py
│   └── speechmatics_provider.py
├── benchmark.py        # Main runner script
└── README.md
```

## Output Example

```
Provider: DEEPGRAM
Duration: 45.32s
Speakers detected: 2 - ['0', '1']

Transcript:
Hello, welcome to the demo...

--- Speaker Segments ---
[0.0s] Speaker 0: Hello, welcome to the demo.
[2.5s] Speaker 1: Thanks for having me.
...
```

## Adding New Providers

1. Create a new file in `providers/` (e.g., `whisper_provider.py`)
2. Implement the `STTProvider` abstract class
3. Add to `providers/__init__.py`
4. Add to `benchmark.py` provider choices

## API Documentation

- Deepgram Diarization: https://developers.deepgram.com/docs/diarization
- Deepgram Keyterms: https://developers.deepgram.com/docs/keyterm
- Speechmatics Diarization: https://docs.speechmatics.com/features/diarization
