# FlexiVoice AI — Demo Day Run Sheet

A 3-minute presenter playbook. Stick to it under stage pressure; everything you'll be asked is anticipated below.

---

## 0. Before you stand up (T-15 minutes)

| Step | Why |
|---|---|
| Plug into power. | The 5090 throttles on battery. |
| Open one Chrome window with two pinned tabs: `http://127.0.0.1:8002/emi-reminder` and `http://127.0.0.1:8002/soft-collections`. | One tab per scenario; no clicking around mid-demo. |
| In each tab, press **Ctrl+Shift+R** once. | Drops any stale cached `app.js`. |
| In each tab, **Settings → Site permissions → Microphone → Allow**. | Avoid the permission popup on stage. |
| Test mic with a single "hello, this is a test" on the soft-collections page. End the call. Wait for the outcome card. | Confirms STT + LLM + TTS + outcome classifier are all warm. |
| Open one PowerShell window, leave it visible. | If anything misbehaves you can show the live server log. |
| Set the system clock check: if it's between 7 PM and 8 AM IST, the soft-collections opening will mention being outside the RBI window — that's intentional and a great talking point. | Don't get caught off guard. |

If you're on a hotspot or guest WiFi, double-check the URL works at `http://127.0.0.1:8002/` (localhost; HTTPS not needed). For LAN demos on another laptop you'll need to (a) set `HOST=0.0.0.0` and restart, (b) run `scripts/make_cert.py` to generate a self-signed cert for browser-mic permission, then (c) point the other laptop at `https://<this-machine-LAN-ip>:8002/`.

---

## 1. The opening line (15 seconds)

> "Two of the highest-volume call types in any NBFC are EMI reminders and soft collections — pre-due nudges and early-DPD calls. Together they're hundreds of thousands of calls a day, and one wrong sentence in a collections call is an RBI complaint. So we built a voice agent for exactly those two — fully local on one GPU, with the LLM swappable between **Claude Sonnet 4.5 for quality** and **local Qwen 2.5 for cost**, and with **RBI Fair-Practice rules enforced in two layers**."

Then point to the screen. Do not explain architecture yet. **Show the demo first.**

---

## 2. Demo 1 — EMI Reminder on Claude (~60 seconds)

Tab: `http://127.0.0.1:8002/emi-reminder`. Default LLM dropdown stays on **Claude Sonnet 4.5**.

1. Walk the audience through the persona card on the left (one sentence each): *"Rajesh Kumar, ₹2.5 L loan, EMI of ₹12,480 due in 3 days. We're calling to remind him."*
2. Hit **Start Call**.
3. **Wait silently** while the bot opens. Don't talk over it.
4. When the bot pauses, say: *"Yes, I'm planning to pay on time."*
5. The bot will thank you and ask if there's anything they can help with.
6. Say: *"Actually, I might be travelling that day — can we move it by a week?"*
7. Bot will explain there's a one-time EMI date-change available, ask if you'd like to proceed.
8. Say: *"Yes please, do it."*
9. Hit **End Call**.

While the outcome card loads (~10–15 s), say:

> "Notice the header pill — those are the **actual tokens consumed and the actual cost** for that call, taken straight from Anthropic's response. ~3,000 input tokens, ~250 output tokens. **About 1 cent.**"

When the outcome card appears, point at:

- **Outcome: `partial_commitment`** (or `commitment` if you said yes to the EMI move)
- **Sentiment: positive**
- **RBI violations: 0**
- **Tokens: <number> · $0.0091**
- **STT / LLM / TTS / Total turn** latency numbers

---

## 3. Demo 2 — Soft Collections on Qwen (~75 seconds)

Tab: `http://127.0.0.1:8002/soft-collections`. **Flip the LLM dropdown to Qwen 2.5 (local).**

Walk the persona: *"Anjali Sharma, EMI of ₹8,500 — 5 days overdue. This is the dangerous call. One word wrong and FlexiLoans is on the front page of Mint."*

1. Hit **Start Call**.
2. Bot opens with a polite, compliant intro identifying itself and the purpose.
3. When prompted, say: *"I lost my job last month. I can't pay the full amount."*
4. Bot will express empathy and ask about your situation.
5. Say: *"Can you give me more time? Maybe pay half this month and half next month?"*
6. Bot will offer one of the three restructuring options (partial payment / one-month deferral / EMI extension) — let it.
7. Say: *"Yes, let's do the partial plan."*
8. Bot will confirm the plan and end the call gracefully.
9. Hit **End Call**.

While the outcome card loads (~1–2 s — much faster on Qwen), say:

> "Same demo, same compliance, same outcome quality — but the header pill is now **$0.0000**. Nothing left the box. The bot ran on the GPU sitting under this table."

When the outcome card appears, point at:

- **Outcome: `partial_commitment`**
- **Restructuring option accepted: `partial`**
- **RBI violations: 0** — even though we deliberately triggered an emotional conversation.
- **Tokens: <number> · $0.0000**

---

## 4. The 30-second architecture pitch

> "Behind the scenes:
> - Mic audio runs through a browser-side **AudioWorklet** and an **energy-based VAD** — 350 ms pre-buffer so we don't clip the first phoneme.
> - Audio goes over **one WebSocket** to a FastAPI server.
> - **`faster-whisper distil-large-v3`** does STT — about 500 ms warm.
> - The LLM streams back **token-by-token**. The moment we see a `.` or `!` we cut a sentence and ship it to **Kokoro-82M**, which generates audio in ~100 ms. So the user hears the bot start speaking before the LLM has finished generating.
> - Compliance is enforced **twice** — once in the system prompt, once in a post-generation regex. Every substitution increments the violations counter you saw on the card.
> - End of call, the full transcript goes through one more LLM pass with a strict-JSON schema to produce the outcome.
> - And the **same outcome card** shows the cost, the tokens, the latency, the violations, and the next action — every claim on a slide, rendered by the demo."

---

## 5. The questions you will be asked

### "What if Ollama is offline / what's your fallback?"
- The LLM dispatcher in `server/llm.py` can swap backends per-session at runtime. If Ollama isn't running, the dropdown's Qwen option simply fails fast and the operator picks Claude. If Claude's CLI isn't installed, Qwen is the default. **Either backend on its own is sufficient to run the demo.**

### "How do you stop the bot from saying something illegal?"
- Two layers. Show `server/guardrails.py`. Walk through `FORBIDDEN_PATTERNS` (substitutions like "legal action" → "appropriate next steps per RBI guidelines") and `HARD_BLOCK_PATTERNS` (threats of violence → entire utterance replaced with a fallback). **Every violation is logged and visible on the outcome card.**

### "How does this scale?"
- This is one process on one GPU. Production layout: **STT/TTS worker pools per GPU** (Triton or vLLM), **Redis-backed session state**, **dispatcher with sticky routing**. The single-process design was a hackathon trade-off; the bounded boundaries (`server/stt.py`, `server/tts.py`, `server/llm.py`) already enforce the contract a worker pool would need.

### "Hindi / regional languages?"
- The architecture has no English assumption baked in. V2 swap: full `Whisper-large-v3` (not the distilled English-only variant) for STT, and `IndicTTS` or `Indic Parler-TTS` for output. We chose English-only for hackathon scope because all of you understand it.

### "What about real phone calls?"
- The pipeline is already PCM-in / WAV-out. A μ-law 8 kHz transcoder in `server/telephony.py` plugs directly into Exotel / Twilio / Plivo. **The decision boundary is one adapter file, not a re-architecture.**

### "How accurate is the outcome classifier?"
- Strict JSON schema, `temperature=0`. Returns `call_incomplete` for fewer than 2 turns or 6 user words — anti-hallucination guard. The classifier is itself the LLM (either backend), but called separately from the live dialogue so the prompt can be much stricter without hurting conversational flow.

### "What's the cost per call at scale?"
- Show the header pill. **~$0.0091 per call on Claude, $0 on Qwen.** At 100,000 calls/day that's $900/day on Claude or zero on Qwen. The equivalent human call-centre headcount is roughly $40,000/day. **27× cost reduction**, recoverable in weeks.

### "Why didn't you use ElevenLabs / Sarvam / a cloud TTS?"
- Kokoro is MIT-licensed, runs in 500 MB of VRAM, and ships audio in ~100 ms per sentence. Cloud TTS adds 300–800 ms round-trip and a per-character bill. **For a 4-turn call, Kokoro saves about $0.10 and 1.5 seconds.**

---

## 6. Failure modes and recovery

| If this happens | Do this |
|---|---|
| Mic permission denied | Quietly switch to the prepared screen recording (`DEMO_BACKUP` — TBD). |
| "Server error" red bubble | Show the PowerShell window — usually obvious from the log. Most common cause: a stale browser tab connected to a zombie server on a different port. Hard-reload. |
| Outcome card doesn't appear after End Call | Wait the full 15 s — Claude's classifier subprocess can be cold on first call. If still nothing after 30 s, the WS may have died — restart the call. The earlier 8-second client-side timeout that caused this has been fixed; see `server/main.py` and `static/app.js`. |
| LLM rambles too long | Cut it off by clicking **End Call**. The outcome card will still render correctly — the bot's full reply is already in `session.history`. |
| Network goes down mid-Claude-call | Switch the LLM dropdown to Qwen and start a fresh call. **No reboot needed.** |

---

## 7. Closing line (15 seconds)

> "Two demos, two LLMs, one architecture, one GPU. Cost is on screen. Latency is on screen. Compliance is on screen. The next step is the same architecture talking to **Exotel** for real calls and **AI4Bharat IndicTTS** for Hindi and Marathi. The repo's at `github.com/harshwasan/FLEXIVOICE-AI`. Happy to answer questions."

Stop talking. Wait. Smile.
