# Setup — one-shot

This guide assumes Windows 11 + RTX 5090/4090/3090 + NVIDIA driver installed.

## 1. Install Ollama

Download and install: <https://ollama.com/download/windows>

After install, open a fresh PowerShell:

```powershell
ollama pull qwen2.5:7b-instruct
ollama run qwen2.5:7b-instruct "Say hello in one sentence."
```

The first `pull` is ~4.5 GB. The `run` smoke-tests it. Ctrl-D to exit.

## 2. Python env

```powershell
cd d:\projects\FlexiLoans
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PyTorch installed CPU-only by default, force the CUDA build:

```powershell
pip install --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

Verify CUDA:

```powershell
python -c "import torch; print('cuda?', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

## 3. Pre-warm models (optional but recommended)

```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('distil-large-v3', device='cuda', compute_type='float16')"
python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
```

First run downloads:
- distil-large-v3: ~1.5 GB
- Kokoro: ~330 MB

## 4. Run

```powershell
.\run.bat
```

Or:

```powershell
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>. Allow microphone permission.

## 5. Smoke-test the call

1. Click **EMI Reminder Call**.
2. Read the persona card on the left (you are Rajesh Kumar).
3. Click **Start Call**. Priya should greet you in ~2–3 s after model warm-up.
4. **Hold** the mic button (or hold the **Spacebar**), say "Yes, I'll pay on time", release.
5. Priya should reply within ~1.5 s.
6. Click **End Call** to see the structured outcome + latency metrics.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "ollama: command not found" | Restart PowerShell, or `& 'C:\Users\<you>\AppData\Local\Programs\Ollama\ollama.exe' pull qwen2.5:7b-instruct` |
| `cuda?` shows False | Force-reinstall PyTorch CUDA wheel (step 2) |
| Mic permission denied | Browser will prompt; if blocked, reset under site settings |
| First turn is slow (~10 s) | Models warming. Subsequent turns are fast. |
| "no speech detected" | Hold the mic longer; speak louder; check input device in browser |
| Audio crackle | Other apps using the GPU? Close them. |
| Ollama "model not found" | `ollama pull qwen2.5:7b-instruct` |

## VRAM budget at runtime

| Component | Approx VRAM |
|---|---|
| faster-whisper distil-large-v3 (fp16) | ~1.5 GB |
| Kokoro-82M | ~0.5 GB |
| Ollama qwen2.5:7b-instruct (Q4_K_M) | ~5–6 GB |
| **Total** | **~7–8 GB** |

You have ~16 GB headroom on 24 GB. Swap in `qwen2.5:14b-instruct` for higher quality
(edit `MODEL_NAME` in `server/llm.py`).
