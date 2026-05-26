"""
Speech-to-text using faster-whisper running locally on GPU.

The browser streams 16 kHz mono 16-bit PCM over a WebSocket. We accumulate
chunks until the client signals "end of utterance" (it does its own VAD in the
browser via the AudioWorklet for low-latency endpointing). The server can also
do a defensive VAD pass with silero-vad to strip leading/trailing silence.
"""

from __future__ import annotations
import asyncio
import io
import logging
import os
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_NAME = "distil-large-v3"
_LOCAL_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "whisper-distil-large-v3"
_DEVICE = "cuda"
_COMPUTE_TYPE = "float16"

_model = None
_model_lock = asyncio.Lock()


def _resolve_model_path() -> str:
    """Prefer the locally downloaded copy; fall back to the HF hub id."""
    if _LOCAL_MODEL_DIR.exists() and any(_LOCAL_MODEL_DIR.iterdir()):
        return str(_LOCAL_MODEL_DIR)
    return _MODEL_NAME


async def get_model():
    """Lazy-load the Whisper model on first use."""
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        path = _resolve_model_path()
        logger.info("Loading faster-whisper from %s on %s (%s)", path, _DEVICE, _COMPUTE_TYPE)
        from faster_whisper import WhisperModel

        def _load():
            return WhisperModel(
                path,
                device=_DEVICE,
                compute_type=_COMPUTE_TYPE,
            )

        _model = await asyncio.to_thread(_load)
        logger.info("Whisper model loaded.")
        return _model


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Convert raw 16-bit PCM bytes to a float32 numpy array in [-1, 1]."""
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    return audio


def trim_silence(audio: np.ndarray, sample_rate: int = 16_000, threshold: float = 0.01) -> np.ndarray:
    """Cheap energy-based silence trim. Good enough as a backstop."""
    if audio.size == 0:
        return audio
    abs_audio = np.abs(audio)
    above = np.where(abs_audio > threshold)[0]
    if above.size == 0:
        return audio
    start, end = above[0], above[-1] + 1
    pad = int(0.1 * sample_rate)
    return audio[max(0, start - pad) : min(audio.size, end + pad)]


async def transcribe_pcm16(
    pcm_bytes: bytes,
    sample_rate: int = 16_000,
    language: Optional[str] = "en",
) -> str:
    """Transcribe raw 16-bit mono PCM bytes. Returns plain text."""
    if not pcm_bytes:
        return ""

    audio = pcm16_to_float32(pcm_bytes)
    audio = trim_silence(audio, sample_rate)
    if audio.size < sample_rate * 0.2:
        return ""

    model = await get_model()

    def _run():
        segments, _info = model.transcribe(
            audio,
            language=language,
            beam_size=1,
            best_of=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    text = await asyncio.to_thread(_run)
    return text


def pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16_000) -> bytes:
    """Wrap raw PCM bytes in a WAV container (useful for debugging)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
