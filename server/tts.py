"""
Text-to-speech using Kokoro-82M.

Kokoro is an 82M-param English TTS model. Very fast, very high quality, MIT-licensed.
We pick a warm female voice ('af_heart') by default to match the agent persona "Priya".

Output: 24 kHz mono Float32 -> we convert to 16-bit PCM and wrap in a WAV
container so the browser can play it with a plain <audio> tag or AudioContext
decodeAudioData.
"""

from __future__ import annotations
import asyncio
import io
import logging
import wave

import numpy as np

logger = logging.getLogger(__name__)

VOICE = "af_heart"
SAMPLE_RATE = 24_000

_pipeline = None
_lock = asyncio.Lock()


async def get_pipeline():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    async with _lock:
        if _pipeline is not None:
            return _pipeline
        logger.info("Loading Kokoro TTS pipeline (lang_code='a' = American English)")
        from kokoro import KPipeline

        def _load():
            return KPipeline(lang_code="a")

        _pipeline = await asyncio.to_thread(_load)
        logger.info("Kokoro pipeline loaded.")
        return _pipeline


async def synthesize_wav(text: str, voice: str = VOICE) -> bytes:
    """Synthesize a single sentence/phrase into a WAV byte blob."""
    if not text.strip():
        return b""

    pipeline = await get_pipeline()

    def _run() -> np.ndarray:
        audio_chunks: list[np.ndarray] = []
        generator = pipeline(text, voice=voice, speed=1.05)
        for _gs, _ps, audio in generator:
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            audio_chunks.append(np.asarray(audio, dtype=np.float32))
        if not audio_chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(audio_chunks)

    audio = await asyncio.to_thread(_run)
    return _float32_to_wav_bytes(audio, SAMPLE_RATE)


def _float32_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Convert float32 [-1, 1] mono audio to 16-bit PCM WAV bytes."""
    if audio.size == 0:
        return b""
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()
