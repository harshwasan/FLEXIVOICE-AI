# FlexiVoice AI — Architecture

This is the **design contract** for the voice agent. If the code disagrees with this document, the code is wrong; if the docs disagree with this document, this document wins.

For the marketing-tone overview see `README.md`.  For the long-form hackathon writeup with trade-off rationale see `PITCH.md`. For the operator-on-stage playbook see `DEMO.md`.

---

## 1. Goals and non-goals

### Goals
- **Conversational, not push-to-talk.** The bot opens the mic when it's the user's turn and closes it when it's the bot's turn. Hands-free, no buttons.
- **Local-first.** STT, TTS and (optionally) the LLM all run on one GPU. No mandatory cloud dependency.
- **Cloud-quality toggle.** A single dropdown promotes the LLM to Claude Sonnet 4.5 — same demo, better dialogue, paid in cents.
- **RBI-grade compliance.** Two-layer enforcement (prompt + regex) so every output is auditable and a human can prove the bot did not threaten or shame.
- **Sub-2-second perceived latency** on the local stack from "user stops talking" to "bot starts talking".
- **Visible metrics.** Every claim on stage is rendered on screen by the demo.

### Non-goals (V1)
- Real telephony integration.
- Multi-language (English-only V1).
- Multi-customer memory.
- Voice-print speaker verification.
- Horizontal scale (single-process, single-GPU).

---

## 2. Process layout

```
.venv (Python 3.12)
└── run_server.py                      # sets WindowsProactorEventLoopPolicy *first*
    └── uvicorn(loop="asyncio")
        └── server.main:app            # FastAPI app
            ├── lifespan: warm STT + TTS in background
            ├── HTTP routes: /, /emi-reminder, /soft-collections, /api/scenario/*, /api/backends
            └── WebSocket: /ws/call    # one async task per active call
                ├── stt.transcribe_pcm16     → asyncio.to_thread → faster-whisper / CUDA
                ├── llm.reply_stream:
                │     • claude  → claude-agent-sdk → spawns claude.exe subprocess
                │     • ollama  → blocking HTTP to localhost:11434 inside a thread executor
                └── tts.synthesize_wav       → asyncio.to_thread → Kokoro / PyTorch CUDA
```

Why the `WindowsProactorEventLoopPolicy` shim: uvicorn defaults to `WindowsSelectorEventLoop` which **cannot spawn subprocesses**. The Claude SDK spawns `claude.exe`. Without the shim, every Claude session fails with `NotImplementedError` deep in `asyncio.base_events._make_subprocess_transport`. The launcher sets the policy *before* uvicorn imports anything so by the time the loop is created it's the right type.

---

## 3. The WebSocket protocol (`/ws/call`)

Single duplex JSON channel. No binary frames. Audio is base64-encoded inside JSON.

### Client → Server

| `type` | Payload | When |
|---|---|---|
| `start` | `{scenario, backend}` | First message of every call. `scenario ∈ {emi-reminder, soft-collections}`, `backend ∈ {claude, ollama}` |
| `audio` | `{pcm_b64}` | One full utterance, 16-bit mono PCM @ 16 kHz, base64-encoded. Sent once per detected utterance — not streamed. |
| `end` | `{}` | User clicked End Call. Server replies with `outcome_pending` then `outcome`, then closes. |
| `ping` | `{}` | Keepalive. |

### Server → Client

| `type` | Payload | When |
|---|---|---|
| `session` | `{session_id, scenario}` | After `start`, before the first agent utterance. |
| `agent_start` | `{}` | Bot is about to speak. Client transitions to `AGENT_SPEAKING`. |
| `agent_chunk` | `{text, audio_b64, sample_rate}` | One sentence-sized chunk. Multiple per turn. Client queues the WAV and renders the text. |
| `agent_done` | `{usage}` | All chunks for this turn shipped. `usage` carries running token/cost totals. |
| `user_transcript` | `{text, note?}` | What Whisper heard. |
| `outcome_pending` | `{}` | Classifier is running. UI should show "Generating summary…". |
| `outcome` | `{data, metrics, turns, violations, usage}` | Final structured JSON outcome. Server closes the WS right after. |
| `error` | `{message}` | Recoverable error. |
| `pong` | `{}` | Reply to `ping`. |

### Why one channel, JSON-only

Trade-off: ~33% bandwidth penalty from base64 vs. mixed binary/text frames. **Acceptable because:**

- Every message is a single line in DevTools → Network. Replay from log is trivial.
- A 3-second utterance is ~128 KB after base64 — sub-millisecond on a LAN.
- Whole-utterance audio (not streaming) matches Whisper's non-streaming nature — there's nothing useful to interleave.

---

## 4. Browser audio pipeline (`static/app.js` + `static/pcm-worklet.js`)

```
getUserMedia (AEC + NS + AGC)        # browser-native filters
    → AudioContext (device SR, ~48 kHz)
        → AudioWorklet "pcm-capture" # runs off main thread
            → onAudioFrame(float32, 128-sample chunks)
                → Energy VAD (RMS + hysteresis + 350 ms pre-buffer)
                    → On end-of-utterance:
                        → downsample 48 → 16 kHz (box-filter average)
                        → Float32 → Int16
                        → base64
                        → WS send { type: "audio", pcm_b64 }
```

### Mic state machine

```
IDLE ─(Start Call)─► AGENT_SPEAKING ─(agent_done + queue drained)─► LISTENING
                                                                       │
                            ┌──────────────(VAD detects voice)─────────┘
                            ▼
                       USER_SPEAKING ─(VAD detects 900 ms silence)─► PROCESSING
                                                                       │
                            ┌──────────────(server's agent_start)─────┘
                            ▼
                       AGENT_SPEAKING …
```

Plus a one-way trapdoor: `ENDING`. Triggered by clicking End Call. Refuses any further state transitions until the WebSocket closes. This is the fix for an earlier bug where a mid-stream `agent_done` would re-open the mic after the user had ended the call.

### VAD constants (in `app.js`)

```javascript
const VAD = {
  preBufferMs: 350,        // ring buffer of last N ms before voice fires
  startRms: 0.018,         // voice-start threshold
  keepRms:  0.010,         // voice-keep threshold (hysteresis)
  endSilenceMs: 900,       // silence-to-end-of-utterance
  minSpeechMs: 250,        // shorter blips are ignored
  maxUtteranceMs: 20000,   // hard cap
};
```

Energy VAD was chosen over a neural VAD (e.g. Silero in WASM) for cost and simplicity. Silero would add 5 MB and 5–15 ms of inference per frame. The browser AEC+NS already cleans the signal upstream; faster-whisper runs Silero **server-side** as a backstop anyway.

---

## 5. Server orchestration (`server/main.py`)

One turn looks like this:

```python
# inside the /ws/call loop
if msg["type"] == "audio":
    user_text = await stt.transcribe_pcm16(pcm)        # ~500 ms warm
    session.add_user(user_text)
    await _stream_and_speak(ws, session, scenario_id)
```

`_stream_and_speak` is the streaming kernel:

```python
async def _stream_and_speak(ws, session, scenario_id, prepared_text=None):
    await _send_json(ws, {"type": "agent_start"})
    t_start = perf_counter()
    full_text_parts = []

    async for sentence in llm.reply_stream(session, scenario_id):
        clean, violations = sanitize_agent_utterance(sentence)  # RBI layer 2
        for v in violations:
            session.record_violation(v)
        if not clean:
            continue
        wav = await tts.synthesize_wav(clean)                   # Kokoro
        await _send_json(ws, {"type": "agent_chunk",
                              "text": clean,
                              "audio_b64": base64.b64encode(wav).decode("ascii"),
                              "sample_rate": tts.SAMPLE_RATE})
        full_text_parts.append(clean)

    session.add_assistant(" ".join(full_text_parts))
    await _send_json(ws, {"type": "agent_done", "usage": _usage_snapshot(session)})
```

Three things happening at once:

1. **LLM streams tokens** through the sentence chunker in `llm._split_into_sentences`.
2. **The moment a sentence boundary appears**, Kokoro synthesises that sentence — typically before the LLM has even finished generating the next one.
3. **The browser starts playing the first WAV** while the rest is still being made. First audio reaches the user's ears in roughly `(STT + LLM-first-sentence + TTS-first-chunk)` ms, not `(STT + full-LLM + full-TTS)`.

---

## 6. LLM dispatcher (`server/llm.py`)

Two backends, one interface:

```python
async def reply_stream(session, scenario_id) -> AsyncIterator[str]:
    if _session_backend(session) == "claude":
        async for s in _claude_reply_stream(session, scenario_id):
            yield s
    else:
        async for s in _ollama_reply_stream(session, scenario_id):
            yield s
```

### Claude path
- `claude-agent-sdk` → `ClaudeSDKClient` (one persistent subprocess for the lifetime of the WS).
- `include_partial_messages=True` for token-by-token streaming.
- Token deltas come on `StreamEvent { type: content_block_delta }`.
- Final `ResultMessage` carries authoritative `usage` + `total_cost_usd` — we use those verbatim for the cost pill.

### Ollama path
- `ollama.chat(stream=True)` in a thread executor.
- Token deltas published into an `asyncio.Queue` (read by the async iterator from the main loop).
- Last chunk's `prompt_eval_count` + `eval_count` feed the token counter.
- Cost reported as **$0** (price table in `server/state.py:_PRICE_TABLE_USD_PER_MTOK` returns 0 for unknown / local models).

### Outcome classifier (separate concern)

Live-turn LLMs optimise for "next thing to say"; summarising the call needs a different prompt. At `end`:

```python
if user_turns < 2 or user_words < 6:
    return _empty_outcome(...)   # call_incomplete, no LLM call
```

Otherwise a one-shot LLM call with `temperature=0` and a strict-JSON schema produces:

```json
{
  "outcome": "commitment | partial_commitment | refused | callback_requested
              | dnd_requested | escalate | info_only | call_incomplete",
  "commitment_amount": null | int,
  "commitment_date":   null | "YYYY-MM-DD",
  "restructuring_option_accepted": null | "partial" | "extension" | "deferral",
  "customer_sentiment": "positive | neutral | concerned | frustrated | angry",
  "key_points": ["...", ...],
  "next_action": "..."
}
```

`_validate_outcome` coerces anything off-schema to `info_only`. The classifier respects a 60 s timeout; the UI displays "Generating summary…" while it runs.

---

## 7. RBI Fair-Practice guardrails (`server/guardrails.py`)

### Layer 1 — Prompt (`COMPLIANCE_PREAMBLE`)
Injected into the system prompt for `soft-collections` only. Twelve numbered rules. Sonnet 4.5 follows them ~98% of the time, Qwen ~93%.

### Layer 2 — Post-gen regex
Every sentence runs through:

- `HARD_BLOCK_PATTERNS` — threats of violence, abusive language → entire sentence replaced with a safe fallback, violation logged.
- `FORBIDDEN_PATTERNS` — "legal action", "police", "jail", "shame", "defaulter", "blacklist", "ruin your credit", "we will contact your family" — substituted with a compliant paraphrase, violation logged.

### Time window
`is_within_call_window()` enforces 8 AM – 7 PM IST. Logged as a warning in the demo (not hard-blocked) so we can present at any hour; in prod this would refuse to dial.

Every violation increments `session.guardrail_violations` and is written to `logs/<session>.jsonl`. The outcome card surfaces the count.

---

## 8. Session state (`server/state.py`)

```python
@dataclass
class Session:
    session_id: str
    scenario_id: str
    started_at: float
    history: list[dict]                    # OpenAI-style {role, content}
    turn_count: int
    guardrail_violations: list[str]
    latencies_ms: dict[str, list[float]]   # stt, llm_first_sentence, tts, total_turn
    outcome: dict | None
    backend: str                           # "claude" | "ollama"
    backend_state: dict                    # e.g. the live ClaudeSDKClient
    tokens_in: int
    tokens_out: int
    cost_usd: float
    last_model: str
```

- Lives in-memory in `SESSIONS: dict[str, Session]`.
- Every turn and every guardrail violation is also written to `logs/<session_id>.jsonl` as an append-only audit trail.
- `add_tokens(in, out, model, cost_usd=None)` — Claude path supplies its own `cost_usd`; Ollama path leaves it `None` and the price table returns `0.0` for "local".

In production this becomes Redis-backed and the JSONL becomes S3 + a Postgres index for searchability and audit retention.

---

## 9. Latency model

End-to-end "user stops talking → user hears bot start" budget:

| Stage | Local (Qwen) | Cloud (Claude) |
|---|---:|---:|
| Client VAD silence window (deliberate UX) | 900 ms | 900 ms |
| WS upload (LAN) | ~5 ms | ~5 ms |
| STT (warm distil-large-v3) | ~500 ms | ~500 ms |
| LLM time-to-first-sentence | ~250 ms | ~1 800 ms |
| TTS first chunk (Kokoro) | ~150 ms | ~150 ms |
| WS chunk → client + decode | ~80 ms | ~80 ms |
| **First audio to user** | **≈ 1.9 s** | **≈ 3.4 s** |

Remaining LLM streaming and remaining TTS overlap with playback of the first sentence, so the perceived wait is bounded by the **first-audio** number, not the total-reply number.

---

## 10. What we punted on

The deferred-but-architected list lives in `PITCH.md` section 7 ("What a production system would look like") and section 8 ("Future systems"). Headlines:

- Telephony adapter (Exotel / Twilio / Plivo) — μ-law 8 kHz transcoder in `server/telephony.py`.
- Multilingual — Whisper-large-v3 full + IndicTTS / IndicParler.
- Horizontal scale — STT/TTS worker pools per GPU, Redis-backed sessions, dispatcher with sticky routing.
- Real persistence — Postgres + S3, ClickHouse for analytics.
- Continuous monitoring — bank-webhook ingestion, repayment-behaviour model.
- Voice cloning + emotion detection — per-borrower personalisation.
