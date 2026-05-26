"""
FastAPI server tying STT + LLM + TTS together over a WebSocket.

Protocol (server <-> browser):

Client -> Server (JSON):
  { "type": "start", "scenario": "emi-reminder" | "soft-collections" }
  { "type": "audio", "pcm_b64": "<base64 16-bit mono 16kHz PCM>" }   // a single utterance
  { "type": "end" }                                                  // user wants to end the call
  { "type": "ping" }

Server -> Client (JSON):
  { "type": "session", "session_id": "...", "scenario": {...} }
  { "type": "agent_start" }
  { "type": "agent_chunk", "text": "...", "audio_b64": "<wav>" }
  { "type": "agent_done" }
  { "type": "user_transcript", "text": "..." }
  { "type": "outcome", "data": {...}, "metrics": {...} }
  { "type": "error", "message": "..." }
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import llm, stt, tts
from .guardrails import is_within_call_window, sanitize_agent_utterance
from .personas import SCENARIOS, scenario_public_payload
from .state import SESSIONS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Warming models in background...")

    async def _warm():
        try:
            await stt.get_model()
            await tts.get_pipeline()
            logger.info("All models warm.")
        except Exception:
            logger.exception("Warm-up failed (will retry on first request)")

    asyncio.create_task(_warm())
    yield


app = FastAPI(title="FlexiLoans Voice Agent", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/emi-reminder")
async def emi_reminder_page():
    return FileResponse(STATIC_DIR / "emi-reminder.html")


@app.get("/soft-collections")
async def soft_collections_page():
    return FileResponse(STATIC_DIR / "soft-collections.html")


@app.get("/api/scenario/{scenario_id}")
async def get_scenario_api(scenario_id: str):
    if scenario_id not in SCENARIOS:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
    return scenario_public_payload(scenario_id)


@app.get("/api/backends")
async def get_backends_api():
    return {"backends": llm.available_backends(), "default": llm.LLM_BACKEND}


# -----------------------------------------------------------------------------
# WebSocket: one connection per call session
# -----------------------------------------------------------------------------


async def _send_json(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload))
    except Exception:
        pass


async def _stream_and_speak(ws: WebSocket, session, scenario_id: str, prepared_text: str | None = None) -> str:
    """
    Run the LLM (streaming sentences) -> TTS -> send chunks to the client.

    If `prepared_text` is provided, we skip the LLM and just speak that text
    (used for fallback messages). Otherwise we stream sentences from the LLM.
    Returns the full agent utterance (joined).
    """
    await _send_json(ws, {"type": "agent_start"})
    t_start = time.perf_counter()
    full_text_parts: list[str] = []

    async def speak_sentence(sentence: str, t_llm_first: float | None) -> None:
        clean, violations = sanitize_agent_utterance(sentence)
        for v in violations:
            session.record_violation(v)
        if not clean:
            return
        t_tts_start = time.perf_counter()
        wav_bytes = await tts.synthesize_wav(clean)
        session.record_latency("tts", (time.perf_counter() - t_tts_start) * 1000)
        if t_llm_first is not None and not full_text_parts:
            session.record_latency("llm_first_sentence", (t_llm_first - t_start) * 1000)
        full_text_parts.append(clean)
        await _send_json(ws, {
            "type": "agent_chunk",
            "text": clean,
            "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
            "sample_rate": tts.SAMPLE_RATE,
        })

    if prepared_text is not None:
        await speak_sentence(prepared_text, t_llm_first=time.perf_counter())
    else:
        first = True
        t_llm_first = None
        async for sentence in llm.reply_stream(session, scenario_id):
            if first:
                t_llm_first = time.perf_counter()
                first = False
            await speak_sentence(sentence, t_llm_first)

    full_text = " ".join(full_text_parts).strip()
    if full_text:
        session.add_assistant(full_text)
    session.record_latency("total_turn", (time.perf_counter() - t_start) * 1000)
    await _send_json(ws, {
        "type": "agent_done",
        "usage": _usage_snapshot(session),
    })
    return full_text


@app.websocket("/ws/call")
async def call_socket(ws: WebSocket):
    await ws.accept()
    session = None
    scenario_id = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json(ws, {"type": "error", "message": "invalid json"})
                continue

            mtype = msg.get("type")

            if mtype == "ping":
                await _send_json(ws, {"type": "pong"})
                continue

            if mtype == "start":
                scenario_id = msg.get("scenario")
                if scenario_id not in SCENARIOS:
                    await _send_json(ws, {"type": "error", "message": f"unknown scenario {scenario_id}"})
                    continue

                backend_choice = (msg.get("backend") or llm.LLM_BACKEND).lower()
                if backend_choice not in {"claude", "ollama"}:
                    backend_choice = llm.LLM_BACKEND

                if scenario_id == "soft-collections":
                    ok, why = is_within_call_window()
                    if not ok:
                        logger.info("Outside RBI call window: %s", why)

                session = SESSIONS.create(scenario_id, backend=backend_choice)
                session.add_system(llm.build_system_prompt(scenario_id))
                await llm.setup_session(session, scenario_id)
                logger.info("Session %s started with backend=%s", session.session_id, session.backend)

                await _send_json(ws, {
                    "type": "session",
                    "session_id": session.session_id,
                    "scenario": scenario_public_payload(scenario_id),
                })

                t_opening = time.perf_counter()
                opening = await llm.generate_opening(session, scenario_id)
                session.record_latency("llm_first_sentence", (time.perf_counter() - t_opening) * 1000)
                await _stream_and_speak(ws, session, scenario_id, prepared_text=opening)
                continue

            if mtype == "audio":
                if not session:
                    await _send_json(ws, {"type": "error", "message": "session not started"})
                    continue
                pcm_b64 = msg.get("pcm_b64", "")
                pcm = base64.b64decode(pcm_b64)
                if not pcm:
                    continue

                t_stt = time.perf_counter()
                user_text = await stt.transcribe_pcm16(pcm, sample_rate=16_000, language="en")
                session.record_latency("stt", (time.perf_counter() - t_stt) * 1000)

                if not user_text:
                    await _send_json(ws, {
                        "type": "user_transcript",
                        "text": "",
                        "note": "no speech detected",
                    })
                    continue

                await _send_json(ws, {"type": "user_transcript", "text": user_text})
                session.add_user(user_text)

                await _stream_and_speak(ws, session, scenario_id)
                continue

            if mtype == "end":
                if not session:
                    break
                # Tell the client the outcome is being computed so it doesn't
                # decide we're hung and slam the socket shut. The Claude path
                # in particular can take 8-15 s on the first classification.
                await _send_json(ws, {"type": "outcome_pending"})
                try:
                    outcome = await asyncio.wait_for(
                        llm.classify_outcome(session, scenario_id),
                        timeout=60.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Outcome classification timed out for %s", session.session_id)
                    outcome = {
                        "outcome": "info_only",
                        "commitment_amount": None,
                        "commitment_date": None,
                        "restructuring_option_accepted": None,
                        "customer_sentiment": "neutral",
                        "key_points": ["Outcome classifier timed out — see full transcript."],
                        "next_action": "Review the call recording manually.",
                    }
                session.set_outcome(outcome)
                await _send_json(ws, {
                    "type": "outcome",
                    "data": outcome,
                    "metrics": _avg_metrics(session),
                    "turns": session.turn_count,
                    "violations": session.guardrail_violations,
                    "usage": _usage_snapshot(session),
                })
                break

            await _send_json(ws, {"type": "error", "message": f"unknown type {mtype}"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
    except Exception:
        logger.exception("WebSocket error")
        await _send_json(ws, {"type": "error", "message": "server error"})
    finally:
        if session:
            try:
                await llm.teardown_session(session)
            except Exception:
                logger.exception("teardown_session failed")
            SESSIONS.close(session.session_id)


def _usage_snapshot(session) -> dict:
    """A flat usage dict suitable for the websocket payload + the outcome card."""
    turns = max(session.turn_count, 1)
    return {
        "tokens_in": session.tokens_in,
        "tokens_out": session.tokens_out,
        "tokens_total": session.tokens_in + session.tokens_out,
        "cost_usd": round(session.cost_usd, 6),
        "cost_usd_per_turn": round(session.cost_usd / turns, 6),
        "model": session.last_model,
        "turns": session.turn_count,
    }


def _avg_metrics(session) -> dict:
    out = {}
    for stage, vals in session.latencies_ms.items():
        if vals:
            out[stage] = {
                "avg_ms": round(sum(vals) / len(vals), 1),
                "p50_ms": sorted(vals)[len(vals) // 2],
                "n": len(vals),
            }
    return out
