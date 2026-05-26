"""Quick end-to-end check that all three components work."""
from __future__ import annotations
import sys, time, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("[1/3] Loading Whisper...", flush=True)
t = time.time()
from faster_whisper import WhisperModel
from pathlib import Path
LOCAL = Path("models/whisper-distil-large-v3")
src = str(LOCAL) if LOCAL.exists() else "distil-large-v3"
w = WhisperModel(src, device="cuda", compute_type="float16")
print(f"  Whisper OK in {time.time()-t:.1f}s")

print("[2/3] Loading Kokoro...", flush=True)
t = time.time()
from kokoro import KPipeline
pipe = KPipeline(lang_code="a")
out = list(pipe("Hello, this is Priya from FlexiLoans, just calling to check on you.", voice="af_heart", speed=1.05))
import numpy as np
audio = np.concatenate([np.asarray(seg[2].detach().cpu().numpy() if hasattr(seg[2], "detach") else seg[2], dtype=np.float32) for seg in out])
print(f"  Kokoro OK in {time.time()-t:.1f}s, {len(audio)/24000:.2f}s of audio synthesised")

print("[3/3] Testing Ollama chat with qwen2.5:7b-instruct...", flush=True)
t = time.time()
import ollama
resp = ollama.chat(
    model="qwen2.5:7b-instruct",
    messages=[{"role": "user", "content": "In one short sentence, what is an EMI?"}],
    options={"temperature": 0.3, "num_predict": 60},
)
print(f"  Ollama OK in {time.time()-t:.1f}s")
print("  reply:", resp["message"]["content"].strip())

print("\nAll components working. You can run .\\run.bat now.")
