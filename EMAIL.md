# Email — FlexiVoice AI hackathon submission

Two versions below. Both are short on purpose — your reader is busy. Add a personal opening line at the top before sending.

Repo: `https://github.com/harshwasan/FLEXIVOICE-AI`
Demo video: *(record one with OBS; 90 s of you running the demo, with subtitles)*

---

## Version A — Hackathon organisers / judges

**Subject:** FlexiLoans Topic 9 — Voice Agent submission · FlexiVoice AI

Hi *(name)*,

Sharing our submission for **Topic 9 — Voice Agent for Lending**.

**FlexiVoice AI** is a conversational voice agent for the two highest-volume call types in an NBFC's playbook — **EMI reminders** (pre-due) and **soft collections** (early DPD). The whole stack — speech-to-text, the LLM, and text-to-speech — runs on a single GPU. A one-click dropdown swaps the LLM between **Claude Sonnet 4.5** for quality and **local Qwen 2.5** for cost — same demo, different trade-off, both fully working.

What's on the screen during the demo:

- Two end-to-end live scenarios with persona cards, customer profiles and restructuring options.
- A real-time mic with automatic voice-activity detection — no push-to-talk.
- Sentence-by-sentence streaming so the bot starts speaking ~2 s after you stop talking on the local stack, ~3.5 s on Claude.
- **RBI Fair-Practice Code enforced in two layers** — a non-negotiable system-prompt preamble *and* a post-generation regex sanitiser. Every violation increments a counter visible on the outcome card.
- A live **token + cost pill** in the header, ticking up as the call progresses (authoritative from the Claude SDK; $0 on the local backend).
- An end-of-call **structured outcome card** with sentiment, commitments, restructuring options accepted, and a four-stage latency breakdown.

**Repo:** https://github.com/harshwasan/FLEXIVOICE-AI
**Setup:** Python 3.12 venv + `pip install -r requirements.txt` + `run.bat`. README has the one-shot setup; ARCHITECTURE.md is the design contract; DEMO.md is the 3-minute presenter run-sheet.

Happy to demo live on a call or in person whenever convenient.

Thanks,
Harsh
*(your-email · your-phone)*

---

## Version B — FlexiLoans internal team / product folks

**Subject:** FlexiVoice AI — voice agent prototype for EMI reminders + soft collections

Hi *(name)*,

Posting this here in case it's useful for the voice-agent track. Quick summary, then the repo link.

**Problem we picked.** EMI reminder calls and DPD 1-7 soft-collections calls. Together those are the bulk of the outbound voice volume at any NBFC, and they're scripted enough to bot. We deliberately stayed *out* of hard-collections / recovery — those still need a human, and a bot misstep there is an RBI complaint.

**What's in the box.**
- Two demo pages, end-to-end, browser-to-browser. No telephony adapter yet — the pipeline is already PCM-in / WAV-out, so a μ-law 8 kHz transcoder plugs into Exotel / Twilio without re-architecture.
- Whisper distil-large-v3 for STT, Kokoro-82M for TTS — both local, ~500 ms and ~100 ms warm respectively.
- LLM dispatcher with **two interchangeable backends** — Claude Sonnet 4.5 (high quality, ~1¢/call) and Qwen 2.5 7B (local, $0/call). Single dropdown switch per session.
- Sentence-level streaming throughout — first audio plays before the LLM has finished generating.
- RBI Fair-Practice rules in `server/guardrails.py`: 12-rule system-prompt preamble + a post-gen regex layer that substitutes / hard-blocks anything the LLM might slip through. Every substitution is logged.
- Per-call telemetry: tokens, USD cost, four-stage latency breakdown, RBI violations counter — all rendered on the outcome card at end-of-call.

**Numbers, warm models, single 5090.**
- "User stops talking → bot starts talking": **≈ 1.9 s on Qwen, ≈ 3.4 s on Claude.**
- Per-call LLM cost: **$0 on Qwen, ~$0.009 on Claude.**
- At 100k calls/day: $900/day on Claude, $0 on Qwen. Equivalent human-agent cost is ~$40k/day.

**What's deferred.** Hindi/regional language support (the architecture has zero English assumption), real telephony, multi-call concurrency, persistent customer memory. All of these are explicit V2 items in `PITCH.md` with concrete model + service choices, not hand-waving.

**Repo:** https://github.com/harshwasan/FLEXIVOICE-AI
**Important docs in there:** `README.md` (overview), `ARCHITECTURE.md` (design contract), `DEMO.md` (3-minute presenter script), `PITCH.md` (long-form writeup with the production roadmap and cost model).

Would love feedback. Happy to walk through it live or send a recording.

Thanks,
Harsh
*(your-email · your-phone)*

---

## Notes for sending

- Personalise the first line — recipient's name, what you spoke about last, why you think they'll care. Generic emails get deleted.
- Attach **nothing**. Everything they need is at the repo URL. Attachments get filtered.
- Send during business hours IST. A "you wrote at 1 AM" timestamp says "this was last-minute".
- If you have a 60-90 s screen recording, link it (Loom / YouTube unlisted) in the body **above** the repo link. Most people will click the video, not the repo.
- If you're applying for a role: include one line about *"happy to discuss the trade-offs in person — I have specific opinions about how this scales to telephony and multilingual"* — invites the follow-up call without asking for it directly.
