# FlexiVoice AI

> **An AI-native voice agent for Indian lending.**  Truly conversational, RBI-compliant, and runs fully on one local GPU — with a one-click toggle to switch the LLM brain between **Claude Sonnet 4.5** and **local Qwen 2.5**.

Built for **FlexiLoans Mumbai Tech Week 2026** — Topic 9: *Voice Agent for Lending*.

`STT · LLM · TTS` &nbsp;·&nbsp; `Local-first` &nbsp;·&nbsp; `RBI Fair-Practice guardrails` &nbsp;·&nbsp; `Sentence-streaming` &nbsp;·&nbsp; `Real-time VAD`

---

## Quickstart

### Prerequisites

- **NVIDIA GPU with ≥ 12 GB VRAM** (24 GB recommended; the demo was built on an RTX 5090).
- **Python 3.12** (`python --version`).
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** (optional, only for the Claude backend — used for auth, no API key needed).
- **[Ollama](https://ollama.com/download)** (optional, only for the Qwen fallback backend).

That's it. **No Docker. No Postgres. No cloud account.** Whisper, Kokoro and the orchestration all run from one Python process.

### 1. One-shot setup

```powershell
git clone https://github.com/harshwasan/FLEXIVOICE-AI.git
cd FLEXIVOICE-AI

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# (Optional) pre-download local model snapshots so first call isn't laggy
python scripts\download_models.py

# (Optional) pull the Qwen model into Ollama
ollama pull qwen2.5:7b-instruct
```

### 2. Run

```powershell
.\run.bat
```

Open the URL the launcher prints (defaults to `http://127.0.0.1:8002/`).

> Behind the scenes `run.bat` calls `python run_server.py`, which sets `WindowsProactorEventLoopPolicy` *before* uvicorn boots — required because the Claude SDK spawns a subprocess and Windows' default selector loop can't.

---

## What is this?

FlexiVoice AI is a **truly conversational voice agent** for the two highest-volume call types in an NBFC's playbook:

| Demo page | Scenario | What's interesting |
|---|---|---|
| **`/emi-reminder`** | Rajesh Kumar, EMI of ₹12,480 due in 3 days | Short, polite, no-upsell; the customer should feel served, not sold to. |
| **`/soft-collections`** | Anjali Sharma, ₹8,500 EMI 5 days overdue | Empathetic, RBI-compliant, can offer one of three restructuring options. Compliance is enforced in **two** layers (system prompt + post-gen regex) so every output is auditable. |

Per call, the operator picks:

- **Claude Sonnet 4.5** — best dialogue quality, ~4 s/turn, ~$0.01/call.
- **Qwen 2.5 7B** (Ollama) — fully local, ~1 s/turn, **$0/call**.

Both backends share **one streaming interface** in `server/llm.py`. The choice is per-session and persists in `localStorage`.

> See **`ARCHITECTURE.md`** for the full design contract, **`DEMO.md`** for the 3-minute presenter script, and **`PITCH.md`** for the long-form hackathon writeup.

---

## Demo flow (~3 minutes)

1. Open `http://127.0.0.1:8002/`.
2. Click into **EMI Reminder** → keep the default Claude backend → **Start Call**.
3. Have a natural conversation: "Yes I can pay" → "Actually can I get a one-month extension?".
4. **End Call** — the outcome card renders in ~10 s with sentiment, key points, RBI violations, tokens consumed, and **estimated $ cost**.
5. Open **Soft Collections** → flip the dropdown to **Qwen 2.5 (local)** → repeat.  The header pill now ticks up to `$0.0000` instead of `$0.0091` — the air-gapped vs. cloud trade-off is on screen.

---

## Demo recordings

Pre-recorded call walkthroughs are bundled at the repo root as **[`Demo Videos.zip`](./Demo%20Videos.zip)** (~98 MB, contains both clips):

| File inside the zip | Backend | What it shows |
|---|---|---|
| `Emi Reminder Call-Qwen .mp4` | Qwen 2.5 (local) | EMI reminder happy path — fully on-device, ~1 s/turn. |
| `Soft Collections Call Sample.mp4` | Claude Sonnet 4.5 | Soft-collections with restructuring offer + RBI guardrails. |

Download directly: [`Demo Videos.zip`](https://github.com/harshwasan/FLEXIVOICE-AI/raw/main/Demo%20Videos.zip)

---

## Architecture (one turn, end-to-end)

```
┌─────────────────────────── Browser ──────────────────────────────────┐
│  getUserMedia (AEC + NS + AGC)                                        │
│    → AudioWorklet (off main thread)  Float32 @ 48 kHz                 │
│      → Energy VAD (RMS + hysteresis + 350 ms pre-buffer)              │
│        → On end-of-utterance:                                         │
│          → Downsample 48 → 16 kHz, Float32 → Int16 (6× bandwidth cut) │
│          → base64 → JSON WebSocket frame "audio"                      │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼   (single WS, JSON-only)
┌─────────────────────────── Server (FastAPI) ─────────────────────────┐
│  /ws/call session                                                     │
│   ├─ faster-whisper distil-large-v3 (CUDA, FP16, Silero VAD backstop) │
│   ├─ LLM backend (per-session toggle):                                │
│   │     • Claude Sonnet 4.5  (claude-agent-sdk → claude.exe)          │
│   │     • Qwen 2.5 7B        (Ollama, localhost:11434)                │
│   │     → Async iterator of token deltas → sentence chunker           │
│   ├─ Per sentence as it arrives:                                      │
│   │     • RBI sanitiser (regex substitute / hard-block)               │
│   │     • Kokoro-82M TTS → 24 kHz WAV blob                            │
│   │     → ship "agent_chunk" { text, audio_b64 }                      │
│   └─ End of call: full transcript → Claude/Qwen one-shot →            │
│        strict-JSON outcome (tokens · cost · sentiment · violations)   │
└──────────────────────────────────────────────────────────────────────┘
```

All three models run **on one local GPU**. Claude is the only thing that touches the network, and it's optional — flip the dropdown and the entire pipeline becomes air-gapped.

---

## Models — what, why, and how big

| Stage | Model | VRAM | Why this one |
|---|---|---|---|
| **STT** | `faster-whisper distil-large-v3` (FP16, CUDA) | ~1.5 GB | Distilled Whisper-large-v3: ~6× faster than full large-v3 for ~1% WER loss. Handles Indian-accented English well. `vad_filter=True` invokes Silero internally as a backstop. |
| **Live LLM (default)** | `claude-sonnet-4-5` via local Claude Code SDK | 0 (cloud) | Best persona-fidelity and compliance instruction-following. ~4–5 s/turn, hidden behind sentence streaming. Auth piggybacks on the already-installed Claude Code CLI — no API key. |
| **Live LLM (toggle)** | `qwen2.5:7b-instruct` via Ollama | ~7 GB | First-token in ~250 ms, full turn in ~1 s. Quality is "good enough" for the 3–4-turn scripts in scope. **Zero cost, zero egress.** |
| **TTS** | `Kokoro-82M` (voice `af_heart`, 24 kHz) | ~0.5 GB | 82 M params → ~100 ms per sentence on a 5090. MIT-licensed. Warm female voice that matches the "Priya" persona. |
| **VAD (client)** | Custom energy + hysteresis | 0 | One multiply-add per sample; 350 ms pre-buffer so plosives aren't clipped. |
| **VAD (server)** | Silero VAD (inside faster-whisper) | trivial | Strips any residual silence the client VAD let through. |

Total local footprint: **~10 GB VRAM steady state.** Comfortable on a 24 GB card with headroom for parallel calls.

---

## Repository layout

```
FLEXIVOICE-AI/
├── server/                  # FastAPI app — one process, no microservices
│   ├── main.py              # WS protocol, lifespan, /api routes
│   ├── stt.py               # faster-whisper wrapper
│   ├── tts.py               # Kokoro wrapper
│   ├── llm.py               # Claude + Ollama dispatcher, sentence streaming
│   ├── personas.py          # Customer profiles, agent objectives per scenario
│   ├── guardrails.py        # RBI Fair-Practice Code: preamble + regex sanitiser
│   └── state.py             # Per-session memory, token+cost accounting, JSONL logs
├── static/                  # Vanilla HTML/CSS/JS — no build step
│   ├── index.html           # Landing
│   ├── emi-reminder.html    # Demo 1
│   ├── soft-collections.html # Demo 2
│   ├── app.js               # WS client, VAD state machine, audio playback queue
│   ├── pcm-worklet.js       # AudioWorklet processor (mic frame capture)
│   └── style.css
├── scripts/
│   ├── download_models.py   # Pre-download Whisper + Kokoro to local dirs
│   ├── make_cert.py         # Self-signed TLS for LAN demos
│   ├── smoke_test.py        # Verify STT + LLM + TTS load
│   └── test_claude.py       # Latency probe for the Claude SDK
├── run_server.py            # Launcher — sets WindowsProactorEventLoopPolicy
├── run.bat                  # Windows one-click runner
├── requirements.txt
├── ARCHITECTURE.md          # Detailed design + trade-offs
├── DEMO.md                  # 3-minute presenter script
├── PITCH.md                 # Long-form hackathon writeup
├── SETUP.md                 # Detailed Windows setup notes
└── README.md
```

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web server | **FastAPI** + Python 3.12 | Async-first, native WebSockets, OpenAPI for free. |
| LLM (cloud) | **Anthropic Claude Sonnet 4.5** via `claude-agent-sdk` | Best persona fidelity; auth via local Claude Code CLI, no key plumbing. |
| LLM (local) | **Qwen 2.5 7B Instruct** via **Ollama** | One `ollama pull`; ~1 s/turn on a 5090. |
| STT | **`faster-whisper`** (CTranslate2) running **distil-large-v3** | Fastest open Whisper runtime; FP16 CUDA. |
| TTS | **Kokoro-82M** | MIT-licensed, ElevenLabs-class quality at ~100 ms/sentence. |
| Realtime | **Native WebSocket** (no SignalR / no Socket.io) | Single duplex channel; JSON-only payloads keep replay logs human-readable. |
| Frontend | **Vanilla HTML/CSS/JS + AudioWorklet** | No build step. View-source friendly for judges. |
| Audio | **WebAudio** + custom energy VAD | Sub-millisecond per-frame; 350 ms ring buffer for plosive capture. |
| Persistence | **JSONL** per session in `logs/` | Append-only audit; no DB to provision; trivially grep-able. |

---

## Live measurements (rendered on the outcome card)

Every call produces a structured outcome with:

- `outcome` — one of `commitment · partial_commitment · refused · callback_requested · dnd_requested · escalate · info_only · call_incomplete`
- `customer_sentiment` · `commitment_amount` · `commitment_date` · `restructuring_option_accepted`
- **Tokens** (in / out / total) and **estimated USD cost** — authoritative from the Claude SDK, **$0** for Qwen.
- Latency averages for the four stages of one turn: **STT · LLM 1st sentence · TTS / chunk · Total turn**.
- RBI guardrail violations counter (each substitution or hard-block is recorded).

Typical numbers on warm models, 5090, sub-ms LAN:

| Stage | Local stack (Qwen) | Cloud stack (Claude) |
|---|---:|---:|
| STT (warm distil-large-v3) | ~500 ms | ~500 ms |
| LLM time-to-first-sentence | ~250 ms | ~1 800 ms |
| TTS first chunk (Kokoro) | ~150 ms | ~150 ms |
| **User-perceived first audio after end-of-utterance** | **≈ 1.9 s** | **≈ 3.4 s** |
| Per-call LLM cost | **$0.0000** | ~$0.0091 |

---

## Compliance — RBI Fair-Practice Code, baked in

Two enforcement layers in `server/guardrails.py`:

1. **System-prompt preamble** — 12 non-negotiable rules injected before every soft-collections call:
   - Identify the agent, the lender, and the purpose upfront.
   - No threats, no shaming, no defaulter / blacklist / police / jail.
   - 8 AM – 7 PM IST call window.
   - Honour DND immediately and end the call.
   - Always offer at least one restructuring option before escalating.
   - Never disclose loan details to a third party.

2. **Post-generation regex sanitiser** — every sentence the LLM emits is passed through a substitution table before TTS. "Legal action" → "appropriate next steps as per RBI guidelines". Anything matching a hard-block pattern (threats of violence, abusive language) replaces the whole utterance with a safe fallback.

Every substitution increments `session.guardrail_violations` and is logged. Zero hidden failures.

---

## What's intentionally deferred (and why)

| Deferred to V2 | Why not in V1 |
|---|---|
| Hindi / Marathi / Tamil voice agent | We chose English-only for hackathon scope; the architecture has zero English assumptions baked in. |
| HTTPS + WebRTC | Browsers refuse `getUserMedia` on HTTP over a LAN IP. For LAN demos use `chrome://flags/#unsafely-treat-insecure-origin-as-secure` or run `scripts/make_cert.py` to generate a self-signed cert. |
| Real telephony adapter (Exotel / Twilio / Plivo) | Calls in the demo are browser-to-server. The pipeline is already PCM-in / WAV-out; a μ-law 8 kHz transcoder in `server/telephony.py` would plug straight in. |
| Persistent customer memory | Out of scope for one weekend. `logs/<session>.jsonl` is the seed for an embedding index. |
| Multi-call concurrency stress test | Single-process scope. Production layout: one STT/TTS worker pool per GPU, Redis-backed session state, dispatcher pinning calls to workers. |

`PITCH.md` has a full production roadmap, cost model, and the V2-V7 future vision.

---

## Acknowledgements

Built for the **FlexiLoans Mumbai Tech Week 2026** hackathon — Topic 9: *Voice Agent for Lending — Pick Your Use Case(s)*. All customer profiles in the demo dataset are synthetic.
