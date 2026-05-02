# Anki Cards Builder

Build Anki-ready CSV files for vocabulary cards by adding pronunciation audio to rows of words.

The main script supports two modes:

- Full lookup mode: fetch definitions from OneLook when `Back` is empty, try Oxford pronunciation audio, and fall back to local TTS when Oxford audio is unavailable.
- TTS-only mode: generate local TTS audio for every input row and keep the existing `Back` explanation text.

## Files

- `anki_audio_fetcher_with_translations.py` - main CSV processing pipeline.
- `qwen_tts_fallback.py` - local Qwen TTS helper used by the main script.
- `r.sh` - simple example runner for full lookup mode.
- `audio/` - generated or downloaded audio files.

## Input CSV

The input CSV must have a `Front` column containing the word or phrase to pronounce.

Recommended columns:

```csv
Front,Back,Audio,Definition,DL valid
entranceway,Passage providing entry into a place,,,
```

If optional columns are missing, the script creates them:

- `Back` - card back text. In TTS-only mode this should already contain your thesaurus explanation.
- `Audio` - path or source URL for the audio.
- `Definition` - Oxford definition URL in full lookup mode.
- `DL valid` - whether the audio file was successfully downloaded or generated.

When audio succeeds, the script appends an Anki sound tag to `Back`, for example:

```text
Passage providing entry into a place [sound:entranceway__tts.ogg]
```

## Usage

Full lookup mode:

```bash
.venv/bin/python anki_audio_fetcher_with_translations.py --audio_dir audio anki_audio_fetcher.csv anki_audio_fetcher_out.csv
```

TTS-only mode:

```bash
.venv/bin/python anki_audio_fetcher_with_translations.py --tts-only --audio_dir audio anki_audio_fetcher.csv anki_audio_fetcher_out.csv
```

TTS-only mode skips OneLook, Oxford lookup, definition URL checks, and remote audio downloads. It uses `Front` as the text to synthesize and preserves existing `Back` text.

## Local TTS Requirements

The TTS fallback is configured in `anki_audio_fetcher_with_translations.py`:

```python
LOCAL_TTS_FALLBACK_MODEL = "/Users/alekseiplotnitskii/.lmstudio/models/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
LOCAL_TTS_RUNTIME_PYTHON = ".venv-tts/bin/python"
LOCAL_TTS_OUTPUT_FORMAT = "ogg"
```

Required for TTS:

- `.venv-tts` with `mlx_audio` and `huggingface_hub` installed.
- The Qwen3 TTS model at the configured path.
- `ffmpeg` on `PATH` when output format is `ogg`.

The helper generates WAV internally and converts it to OGG for Anki.

## Notes

- Re-running the script will not duplicate an identical `[sound:...]` tag.
- Generated audio, virtual environments, logs, and the default output CSV are ignored by git.
- Full lookup mode uses Selenium and ChromeDriver for OneLook scraping, plus HTTP requests for Oxford audio and definition pages.
