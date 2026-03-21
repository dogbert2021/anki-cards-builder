import argparse
import json
from pathlib import Path

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
    parser.add_argument("--output-dir", required=True, help="Directory where the wav file should be written")
    parser.add_argument("--file-prefix", required=True, help="Output filename prefix without extension")
    return parser.parse_args()


def main():
    args = parse_args()

    model_path = Path(args.model_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{args.file_prefix}.wav"

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

    if not output_path.exists():
        raise SystemExit(f"TTS generation did not create output file: {output_path}")

    payload = {
        "output_path": str(output_path),
        "filename": output_path.name,
    }
    print(f"TTS_RESULT_JSON={json.dumps(payload)}")


if __name__ == "__main__":
    main()
