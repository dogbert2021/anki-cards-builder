import argparse
import json
from pathlib import Path
import shutil
import subprocess

from huggingface_hub import snapshot_download
from mlx_audio.tts.generate import generate_audio


DEFAULT_MODEL_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"


def ensure_speech_tokenizer(model_path: Path, model_repo: str) -> None:
    speech_tokenizer_dir = model_path / "speech_tokenizer"
    if speech_tokenizer_dir.exists():
        return

    snapshot_download(
        repo_id=model_repo,
        allow_patterns=["speech_tokenizer/*"],
        local_dir=str(model_path),
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Generate fallback TTS audio with local Qwen3-TTS.")
    parser.add_argument("--model-path", required=True, help="Path to the local MLX Qwen3-TTS model")
    parser.add_argument("--model-repo", default=DEFAULT_MODEL_REPO, help="HF repo id for missing tokenizer assets")
    parser.add_argument("--text", required=True, help="Text to synthesize")
    parser.add_argument("--output-dir", required=True, help="Directory where the audio file should be written")
    parser.add_argument("--file-prefix", required=True, help="Output filename prefix without extension")
    parser.add_argument(
        "--output-format",
        default="ogg",
        choices=("ogg", "wav"),
        help="Final output audio format",
    )
    return parser.parse_args()

def encode_wav_to_ogg(wav_path: Path, ogg_path: Path) -> None:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise SystemExit("ffmpeg is required to encode TTS fallback audio to ogg")

    result = subprocess.run(
        [
            ffmpeg_path,
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "libvorbis",
            "-q:a",
            "4",
            str(ogg_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"ffmpeg failed to encode ogg: {(result.stderr or result.stdout or '').strip()[-1000:]}"
        )


def main():
    args = parse_args()

    model_path = Path(args.model_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_output_path = output_dir / f"{args.file_prefix}.wav"
    output_path = output_dir / f"{args.file_prefix}.{args.output_format}"

    if output_path.exists():
        payload = {
            "output_path": str(output_path),
            "filename": output_path.name,
        }
        print(f"TTS_RESULT_JSON={json.dumps(payload)}")
        return

    if args.output_format == "ogg" and wav_output_path.exists():
        encode_wav_to_ogg(wav_output_path, output_path)
        wav_output_path.unlink(missing_ok=True)
        payload = {
            "output_path": str(output_path),
            "filename": output_path.name,
        }
        print(f"TTS_RESULT_JSON={json.dumps(payload)}")
        return

    ensure_speech_tokenizer(model_path, args.model_repo)

    generate_audio(
        text=args.text,
        model=str(model_path),
        output_path=str(output_dir),
        file_prefix=args.file_prefix,
        audio_format="wav",
        verbose=False,
        join_audio=True,
        play=False,
    )

    if not wav_output_path.exists():
        raise SystemExit(f"TTS generation did not create output file: {wav_output_path}")

    if args.output_format == "ogg":
        encode_wav_to_ogg(wav_output_path, output_path)
        wav_output_path.unlink(missing_ok=True)
    else:
        output_path = wav_output_path

    payload = {
        "output_path": str(output_path),
        "filename": output_path.name,
    }
    print(f"TTS_RESULT_JSON={json.dumps(payload)}")


if __name__ == "__main__":
    main()
