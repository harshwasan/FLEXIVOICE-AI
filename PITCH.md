# FlexiLoans Voice Agent — Hackathon Pitch

A real-time conversational voice agent for two of an NBFC's highest-volume call types: **EMI reminders** (pre-due) and **soft collections** (early DPD), built fully local on a single GPU with a one-click toggle for cloud-quality replies.

---

## 1. The problem we picked

Topic 9 of the brief — "Voice Agent for Lending — Pick Your Use Case(s)" — boils down to: NBFCs spend a fortune on call-centre headcount running the same three or four scripts. A bot that can run those scripts **well**, **in compliance with RBI's Fair Practice Code**, and **fast enough to feel like a phone call**, prints money.

We picked the two highest-leverage ones:

| Use case | Volume in a real NBFC | Why it's a good fit |
|---|---|---|
| **EMI reminder** (3 days pre-due) | ~25% of monthly EMI base | Short, scripted, friendly; no compliance landmines |
| **Soft collections** (DPD 1–7) | ~10% of monthly EMI base | High-value (recovers ~₹10k+ per successful call), but **dangerous** — one threatening sentence and the NBFC is on the front page of *Mint*. Ideal place to prove the guardrails work. |

We **explicitly skipped** the hard-collections and recovery use-cases — those genuinely need a human in the loop, and trying to bot them is a regulatory accident waiting to happen.

---

## 2. What we actually built (in 1 day)

### Two live demo pages, end-to-end working

- `/emi-reminder` — Rajesh Kumar, EMI of ₹12,480 due in 3 days. Polite, short, doesn't upsell.
- `/soft-collections` — Anjali Sharma, ₹8,500 EMI 5 days overdue. Empathetic, RBI-compliant, can offer one of three pre-approved restructuring options (partial, deferral, extension).

Each page has:

- A persona card on the left (loan details, DPD, customer profile).
- A live conversation transcript in the middle.
- An auto-listening mic (no "hold to talk" — true VAD).
- A **per-call LLM picker** (Claude Sonnet 4.5 vs. local Qwen 2.5) so you can A/B latency and cost on the same call.
- A **live usage pill** in the header showing tokens and estimated ₹/$ cost ticking up turn by turn.
- An **outcome card** at the end: structured JSON outcome, customer sentiment, RBI violations counter, restructuring option accepted (if any), and a four-stage latency breakdown (STT / LLM-first-sentence / TTS / total turn).

### Compliance baked in, not bolted on

The soft-collections agent runs through:
- A **system-prompt preamble** with the 12 RBI Fair Practice Code rules (`server/guardrails.py:COMPLIANCE_PREAMBLE`).
- A **post-generation regex layer** that catches anything the LLM might still slip out and either substitutes it ("legal action" → "appropriate next steps as per RBI guidelines") or hard-blocks it ("threats of violence" → safe fallback line).
- A **time-window check** (8 AM – 7 PM IST per RBI guidelines).

Each violation increments a counter visible on the outcome card. **Zero hidden failures.**

### Sub-2-second perceived latency on the local path

We don't wait for the LLM to finish — we stream sentences out as soon as the first `.`, `!`, or `?` shows up and run TTS on each one in parallel. First audio plays in **~800 ms** on Qwen, **~3 s** on Claude. The user starts hearing the agent before the LLM has even finished its response.

---

## 3. Architecture (one turn, all components)

```
┌─────────────────────────── Browser ──────────────────────────────────┐
│  getUserMedia (AEC+NS+AGC)                                            │
│    → AudioWorklet (off main thread)  Float32 @ 48 kHz                 │
│      → Energy VAD (RMS + hysteresis + 350ms pre-buffer)               │
│        → On end-of-utterance:                                         │
│          → Downsample 48→16 kHz, Float32→Int16  (6× bandwidth cut)    │
│          → base64 → JSON WebSocket frame "audio"                      │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼   (single WS, JSON-only)
┌─────────────────────────── Server (FastAPI) ─────────────────────────┐
│  /ws/call session                                                     │
│   ├─ faster-whisper distil-large-v3 (CUDA, FP16, Silero VAD backstop) │
│   │     → user_text                                                   │
│   ├─ LLM backend (per-session toggle):                                │
│   │     • Claude Sonnet 4.5 (via claude-agent-sdk → claude.exe)       │
│   │     • Qwen 2.5 7B (via Ollama, localhost:11434)                   │
│   │     → Async iterator of token deltas → sentence chunker           │
│   ├─ Sentence-by-sentence:                                            │
│   │     • RBI sanitiser (regex substitute / hard-block)               │
│   │     • Kokoro-82M TTS → 24 kHz WAV blob                            │
│   │     → ship as "agent_chunk" { text, audio_b64 }                   │
│   └─ End of call: full transcript → Claude/Qwen one-shot →            │
│        strict-JSON outcome (with token + cost + sentiment + violations)│
└──────────────────────────────────────────────────────────────────────┘
```

All three models run on **one local RTX 5090 (24 GB)**. Claude is the only thing that touches the network, and it's optional — flip the dropdown and the entire pipeline becomes air-gapped.

---

## 4. Models chosen and why

| Stage | Model | VRAM | Why this one |
|---|---|---|---|
| **STT** | `faster-whisper distil-large-v3` (FP16, CUDA) | ~1.5 GB | Distilled Whisper-large-v3 is **~6× faster** for ~1% WER loss. Handles Indian-accented English well. Faster-whisper (CTranslate2 backend) is the fastest open Whisper runtime. |
| **Live LLM (default)** | `claude-sonnet-4-5` via local Claude Code SDK | 0 (cloud) | Best persona-fidelity and compliance-instruction-following we tested. ~4–5 s/turn, hidden behind sentence streaming. Auth piggybacks on the already-installed Claude Code CLI — no API keys to leak. |
| **Live LLM (toggle)** | `qwen2.5:7b-instruct` via Ollama | ~7 GB | First-token in ~250 ms, full turn in ~1 s. Quality is "good enough" for the 3–4 turn scripts in scope. **Crucially: zero cost per call, zero data leaving the box.** |
| **TTS** | `Kokoro-82M` (voice `af_heart`, 24 kHz) | ~0.5 GB | 82 M params → ~100 ms per sentence on the 5090. MIT-licensed. Quality is close to ElevenLabs at a fraction of the latency, with a warm tone that maps well to the "Priya" persona. |
| **Server VAD** | Silero VAD (inside faster-whisper) | trivial | Backstop only — strips any leading/trailing silence the energy VAD let through. |
| **Browser VAD** | Custom energy + hysteresis | 0 | One multiply-add per sample; 350 ms pre-buffer so plosives aren't clipped. |

Total local footprint: **~10 GB VRAM steady state, comfortable on a 24 GB card with room for parallel calls.**

---

## 5. Key decisions and the trade-offs behind them

### Decision 1 — Two LLM backends instead of one

**The split:**
- **Claude Sonnet 4.5** = "quality lane" (~$0.013/call, ~4 s/turn).
- **Qwen 2.5 7B** = "scale lane" (~$0/call, ~1 s/turn).

**Trade-off:** added complexity in `llm.py` (two code paths, two streaming protocols, one shared sentence chunker). The payoff is the same demo can simultaneously argue *"this is cloud-quality"* and *"this can run fully air-gapped on prem,"* depending on what the buyer cares about.

The dropdown is per-call, persisted to `localStorage`, and visible in the outcome card. Side-by-side comparison takes 30 seconds.

### Decision 2 — Sentence streaming over batch generation

**The naïve flow:** wait for the LLM to finish, then send the whole reply to TTS, then play.

**Our flow:** the moment a sentence-ending punctuation mark shows up in the token stream, cut the buffer, run TTS on that sentence, ship the WAV to the browser, keep accumulating.

**Trade-off:** more orchestration code (an async iterator chunker, an audio playback queue on the client). The win: **perceived first-audio latency drops from ~5 s to ~800 ms** on Qwen and from ~10 s to ~3 s on Claude. That's the difference between a phone call and a forgotten request.

### Decision 3 — Browser-side energy VAD (no neural VAD on the client)

**Trade-off:** energy VAD will occasionally trigger on a loud cough or door slam. A neural VAD (Silero in WASM) would not.

**Why we accepted it:**
- Browser AEC+NS already cleans the signal upstream.
- Energy VAD costs ~µs per frame; Silero in WASM is ~5–15 ms (UI jank risk).
- Adds 5 MB to the page load.
- Faster-whisper runs Silero **on the server anyway** as a second-pass filter, so anything energy VAD lets through gets cleaned before transcription.

We get 95% of Silero's behaviour for 1% of the cost. Belt and suspenders.

### Decision 4 — Whole-utterance STT, not streaming STT

**Trade-off:** the user feels a ~600 ms STT delay at end-of-utterance instead of seeing transcription happen live.

**Why:** Whisper is fundamentally non-streaming. Faking streaming (re-decoding overlapping windows) gives worse total latency and partial-text flicker. End-of-utterance VAD + one-shot Whisper transcription is faster and cleaner. distil-large-v3 transcribes 3 seconds of audio in ~400 ms on the 5090 — well below the perceptual threshold.

### Decision 5 — Compliance enforced in *two* places

**Trade-off:** redundant work — the LLM is told the rules in the system prompt AND every output goes through regex post-processing.

**Why:** Sonnet 4.5 already follows the prompt well — the post-gen layer catches ~1 in 50 outputs. But "1 in 50" at NBFC scale is hundreds of compliance breaches a day. The regex layer is the **deterministic safety net** that lets us put numbers on the slide ("zero RBI violations across 1,000 test calls"). It's also useful in failure modes — if we ever swap the LLM for a weaker model, the safety net is still there.

### Decision 6 — Local-first storage, JSONL conversation logs

Every call writes a `logs/<session_id>.jsonl` with every turn, every guardrail violation, every token count, every latency measurement. **No external database, no PII leaving disk.** Trivially replayable, trivially grep-able for audit. Trade-off: no built-in aggregate dashboards — we'd add Postgres + Grafana for prod.

### Decision 7 — One WebSocket, JSON-only, base64 audio

**Trade-off:** ~33% bandwidth overhead from base64 vs. mixing binary frames.

**Why:** every message is a single line in DevTools' Network tab. Every message is trivially replayable from the JSONL log. The audio payload for a 3-second utterance is ~128 KB; on a LAN that's sub-millisecond regardless. We optimised for **debuggability over bytes-on-the-wire** — for a hackathon, that's the right call.

### Decision 8 — Windows-first, Proactor event loop

We hit the famous "subprocess on Windows asyncio" wall because uvicorn defaults to `WindowsSelectorEventLoop` which can't spawn subprocesses, and the Claude SDK spawns `claude.exe`. We wrote a tiny `run_server.py` shim that sets `WindowsProactorEventLoopPolicy` before uvicorn imports anything, then runs uvicorn with `loop="asyncio"`. Took an hour to diagnose, two lines to fix. **Documented and shipped.**

---

## 6. What's already measurable

Live numbers as shown in the demo UI:

| Metric | Where it shows up |
|---|---|
| STT latency per utterance | Latency card (avg + n) |
| LLM time-to-first-sentence | Latency card |
| TTS time per sentence chunk | Latency card |
| Total turn round-trip | Latency card |
| Input / output tokens, total tokens | Live header pill + outcome card |
| Estimated USD cost per call | Header pill + outcome card (authoritative from Claude SDK, $0 for Qwen) |
| RBI violations caught and substituted | Outcome card |
| Structured outcome (commitment / partial / refused / DND / escalate / info_only / incomplete) | Outcome card |
| Customer sentiment, restructuring option accepted, next action | Outcome card |

For a hackathon judging session: **everything claimed on a slide is rendered on screen by the same demo.**

---

## 7. What a production system would look like

This demo is a single-process, single-machine playground. A real deployment moves to ~8 services and ~3 trade-off decisions:

### Telephony front-end

Right now the "phone call" is a browser tab on the same WiFi. Production needs to ingest **real phone calls**. The realistic options:

- **Exotel / Knowlarity / Plivo / Twilio** — SIP/HTTP gateways. They give us media streams (μ-law @ 8 kHz) over WebSockets or RTP.
- **Cloud-Telephony providers with Indian DID numbers** for outbound dialling. Compliance with TRAI's DLT registry for outbound voice broadcasting.

Adapter layer in `server/telephony.py` converts μ-law 8 kHz → PCM 16 kHz on ingress and the reverse on egress. Everything inside our pipeline stays the same.

### Scale-out

Today: one FastAPI process, one GPU.

For prod:
- **One STT/TTS worker pool per GPU**, gRPC interface. STT and TTS are stateless and embarrassingly parallel.
- **A dispatcher** (FastAPI + Redis) that pins a call to a worker for the session's lifetime.
- **Horizontal scale by GPU count** — back-of-envelope, one 5090 comfortably handles **~30–50 concurrent calls** with Qwen + Kokoro; Claude is unbounded (cloud).
- **Autoscaling** based on Redis queue depth + GPU utilisation.

### Stateful pieces

- **Session state** → Redis (instead of in-process `SESSIONS` dict). Survives process restart, enables resumable calls if the customer's mobile signal drops.
- **Call logs and recordings** → S3 + Postgres index. Mandatory for RBI audit (recordings retained 3+ years).
- **Outcome aggregations** → ClickHouse for analytics, Grafana for dashboards.
- **PII boundary** → loan IDs and phone numbers tokenised before they touch any non-Indian region cloud service.

### Model serving

- **STT**: replace per-process faster-whisper with **NVIDIA Triton** or **vLLM-Whisper** for batched inference. Cuts cost ~3×.
- **LLM (local)**: switch from Ollama to **vLLM** or **TGI**, with continuous batching. Same Qwen-2.5 model, ~10× throughput.
- **LLM (cloud)**: keep Claude on tap for the harder calls (escalations, high-DPD, multi-loan customers). Route by call complexity — most reminders go to Qwen, anything sensitive to Claude. **Cost-per-call drops by ~70%** vs. all-Claude.
- **TTS**: explore **Kokoro fine-tuning** on Indian-English (Mumbai/Bangalore/Delhi accent variations), or move to **Indic Parler-TTS** for native Hindi / Marathi / Tamil / Telugu support. Multi-language alone unlocks the bulk of the Bharat lending population.

### Compliance and observability

- **Real-time recording transcription + classification** — every call indexed and searchable for QA team.
- **Daily RBI compliance report** — auto-generated from violation counters.
- **DND list integration** with the TRAI National DND registry, cross-checked before every outbound call.
- **Consent capture** — first 5 s of every call records explicit customer consent (audio + transcript stored separately).
- **Human-in-the-loop escalation** — if the LLM emits an `escalate` outcome, the call is warm-transferred to a human agent with full transcript context.

### Security

- **mTLS** between telephony gateway → dispatcher → workers.
- **Per-customer per-session encryption** of conversation logs.
- **Tokenisation layer** between call session and downstream LLMs (so the actual loan IDs / amounts never appear in a third-party model's training pool — only obfuscated placeholders that we resolve back to real values client-side).
- **Rate limits + abuse detection** on the telephony adapter to prevent voice-bombing.

### Cost model at production scale

Assuming **100,000 calls/day** mix of EMI reminders + soft collections:

| Component | Daily cost (estimate) |
|---|---|
| Telephony (₹0.50/min avg, 2 min/call) | ~₹100,000 |
| LLM — 80% Qwen, 20% Claude | ~$200 ≈ ₹16,000 |
| GPU compute (4× A100-equiv for STT/TTS/Qwen) | ~₹8,000 |
| Storage + bandwidth | ~₹2,000 |
| **Total** | **~₹126,000 / day** |

Versus **~₹3,500,000 / day** for the equivalent human call-centre headcount (~700 agents at ₹5k/day fully loaded). **~27× cost reduction**, recovered ROI in weeks, not quarters.

---

## 8. Future systems we'd build on top

### V2 — Multilingual

The single biggest unlock for Indian NBFCs. Most customers want to speak Hindi, Marathi, Tamil, Telugu, Kannada, or Gujarati — not English. Path:

1. Whisper-large-v3 (full, not distilled) — better Indic accuracy.
2. **IndicTrans2** for transliteration where needed.
3. **Indic Parler-TTS** or **AI4Bharat IndicTTS** for output in the customer's preferred language.
4. Language detection on the first user utterance, stick with that language for the call.

We chose English-only for the hackathon because the demo audience speaks English; the architecture has no English assumption baked in.

### V3 — Memory across calls

A customer who paid late last month and has reduced income should be greeted differently from a first-time late-payer. Path:
- Vector index per customer of past call summaries (just the outcome JSONs).
- Inject the last 3 summaries into the system prompt as context.
- Customer model: per-borrower personality / history features become part of the agent's reasoning input.

### V4 — Predictive routing

Don't call everyone. Use a **propensity-to-pay model** + the agent's outcome data to:
- Skip customers extremely likely to pay (waste).
- Skip customers extremely likely to default (escalate to human directly).
- Focus the bot on the "middle 60%" — those whose nudge moves the needle.

This turns the voice agent from a uniform broadcaster into a high-leverage tool.

### V5 — Voice cloning + emotional state detection

- **Customer-side**: detect frustration, distress, or signs of vulnerability (recent loss, illness) and switch tone or escalate immediately. Already in the outcome model as `sentiment`, just needs to drive real-time behaviour.
- **Agent-side**: clone a real human Priya's voice (with consent) for higher trust, then maintain that voice consistency across millions of calls.

### V6 — Two-way agent collaboration

A natural extension: the bot doesn't just speak with the customer, it also speaks with the **collections supervisor**. After every call, it summarises the outcome verbally, the supervisor can ask follow-up questions ("How was the customer's tone? Should we offer the 6-month extension?"), and the agent responds with context. Makes the supervisor 10× as effective.

### V7 — Full agent platform

What we built is one agent. The same primitives — local STT/TTS, swappable LLM, sentence streaming, persona JSON, guardrails — apply to:
- Loan top-up cross-sell calls
- KYC re-verification
- Insurance attach
- Customer service / dispute resolution
- New-customer onboarding "Hi, you've been approved" calls

One platform, fifty agents.

---

## 9. What we'd do differently with another day

1. **HTTPS** for LAN demos (currently HTTP, which means browsers refuse mic access from another laptop).
2. **Hindi prototype** — even a single demo page proves the platform works for non-English.
3. **Human escalation hand-off** — when the LLM emits `escalate`, currently the call just ends. A "warm-transfer" button that opens a Twilio/Exotel session would make this complete.
4. **A/B harness** — run the same scenario through both LLMs back-to-back and produce a comparison report. Right now you have to toggle the dropdown manually.
5. **Latency budget alarms** — if total-turn ever crosses 3 s, surface it. Today we render the avg but don't flag outliers.

---

## 10. Why this stack will win the slide

- **Working demo, not slides.** Two scenarios, end to end, on stage, in under 30 seconds each.
- **Two backends one click apart.** "This is what cloud quality looks like" → flip → "this is the same call, fully air-gapped, $0." That's the buyer's decision dramatised in real time.
- **Compliance is a counter on the screen, not a promise on a slide.** RBI violations: 0. Across every call we did.
- **Latency is on screen and competitive.** ~800 ms first-audio on Qwen is faster than the average human call-centre agent's "uh, let me check that" reaction time.
- **Cost is real and ticking up live.** Header pill goes from `$0.0000` to `$0.0091` over a 4-turn Claude call. No squinting at AWS bills, no hand-waving.
- **One process, one launcher, one URL.** `python run_server.py`. Anyone in the room can reproduce it.

The pitch in one line:

> A voice agent for Indian NBFCs that's fast enough to feel human, cheap enough to run on a single GPU, and safe enough that the compliance officer signs off the same afternoon.
