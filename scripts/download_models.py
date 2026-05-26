"""
Download all local models into ./models/ as plain files (no symlinks).

Run once:  .\.venv\Scripts\python.exe scripts\download_models.py

Downloads:
  - Whisper distil-large-v3  (~1.5 GB) -> models/whisper-distil-large-v3/
  - Kokoro-82M               (~330 MB) -> models/kokoro/
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)


def download(repo_id: str, local_dir: Path, allow_patterns: list[str] | None = None) -> None:
    from huggingface_hub import snapshot_download

    print(f"\n=== Downloading {repo_id} -> {local_dir} ===")
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(local_dir),
        allow_patterns=allow_patterns,
        max_workers=4,
    )
    print(f"OK {repo_id}")


def main() -> int:
    try:
        download(
            "Systran/faster-distil-whisper-large-v3",
            MODELS_DIR / "whisper-distil-large-v3",
        )
    except Exception as e:
        print(f"[ERROR] Whisper download failed: {e}", file=sys.stderr)
        return 1

    try:
        download(
            "hexgrad/Kokoro-82M",
            MODELS_DIR / "kokoro",
            allow_patterns=[
                "*.pth",
                "*.json",
                "config.json",
                "kokoro-v*.pth",
                "voices/*.pt",
                "VOICES.md",
            ],
        )
    except Exception as e:
        print(f"[WARN] Kokoro local download failed (will fall back to default cache): {e}", file=sys.stderr)

    print("\nAll done. You can now run:  .\\run.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
