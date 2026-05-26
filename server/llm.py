"""
LLM-driven dialogue management.

Two backends, selected by the LLM_BACKEND env var:
  - "claude" (default): Claude Sonnet 4.5 via the claude-agent-sdk
                        (uses the local Claude Code CLI auth, no API key).
  - "ollama":           Local Ollama (qwen2.5:7b-instruct or similar).

Public API (all async, all take a `Session` from state.py):
  - build_system_prompt(scenario_id) -> str
  - setup_session(session, scenario_id)
  - teardown_session(session)
  - generate_opening(session, scenario_id) -> str       (the bot's first utterance)
  - reply_stream(session, scenario_id) -> AsyncIterator[str]   (sentence-by-sentence)
  - classify_outcome(session, scenario_id) -> dict      (structured wrap-up JSON)
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from typing import AsyncIterator

from .personas import Scenario, get_scenario
from .guardrails import COMPLIANCE_PREAMBLE

logger = logging.getLogger(__name__)

# Backend selection.
#   LLM_BACKEND       : engine for the live voice conversation.
#                       "claude"  -> Claude Sonnet via Claude Code CLI (default,
#                                    streaming deltas; first audio in ~2-4 s)
#                       "ollama"  -> local Qwen (fastest, ~1 s, lower quality)
#   LLM_CLASSIFIER    : engine for the end-of-call outcome analysis (one-shot).
LLM_BACKEND = os.getenv("LLM_BACKEND", "claude").lower()
LLM_CLASSIFIER = os.getenv("LLM_CLASSIFIER", "claude").lower()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

_SENTENCE_END = re.compile(r"([.!?])(\s+|$)")
_OPENING_TRIGGER = (
    "[CALL CONNECTED - the customer just picked up the phone. They have not "
    "said anything yet. Say your opening line now. Just one or two sentences.]"
)


# -----------------------------------------------------------------------------
# Shared: system prompt builder
# -----------------------------------------------------------------------------

def _customer_block(s: Scenario) -> str:
    c = s.customer
    lines = [
        f"- Name: {c.name}",
        f"- Loan ID: {c.loan_id}",
        f"- Phone (masked): {c.phone_masked}",
        f"- Occupation: {c.occupation}",
        f"- City: {c.city}",
        f"- Loan amount: Rs.{c.loan_amount:,}",
        f"- Tenure: {c.tenure_months} months",
        f"- EMI amount: Rs.{c.emi_amount:,}",
        f"- Interest rate: {c.interest_rate}% p.a.",
        f"- Outstanding principal: Rs.{c.outstanding_principal:,}",
        f"- Next/current EMI date: {c.next_emi_date}",
        f"- Last payment date: {c.last_payment_date}",
    ]
    if s.dpd_days:
        lines.append(f"- DPD (days past due): {s.dpd_days}")
    return "\n".join(lines)


def _restructuring_block(s: Scenario) -> str:
    if not s.restructuring_options:
        return ""
    lines = ["You can offer the customer ONE of these restructuring options (pick what fits their situation):\n"]
    for opt in s.restructuring_options:
        lines.append(f"OPTION [{opt.id}] - {opt.name}")
        lines.append(f"  Description: {opt.description}")
        lines.append(f"  Impact: {opt.impact}")
        lines.append("")
    return "\n".join(lines)


def _knowledge_block(s: Scenario) -> str:
    if not s.knowledge_snippets:
        return ""
    lines = ["Reference knowledge you may use when asked:"]
    for k in s.knowledge_snippets:
        lines.append(f"- {k}")
    return "\n".join(lines)


def build_system_prompt(scenario_id: str) -> str:
    s = get_scenario(scenario_id)
    parts = [
        "You are PRIYA, a customer relationship officer at FlexiLoans, an NBFC in India.",
        "You are making an OUTBOUND voice call to the customer below. The customer has just picked up.",
        "",
        "CUSTOMER PROFILE:",
        _customer_block(s),
        "",
        "CALL OBJECTIVE:",
        s.agent_objective,
        "",
    ]

    rs = _restructuring_block(s)
    if rs:
        parts += [rs, ""]

    kn = _knowledge_block(s)
    if kn:
        parts += [kn, ""]

    if scenario_id == "soft-collections":
        parts += ["COMPLIANCE RULES (NON-NEGOTIABLE):", COMPLIANCE_PREAMBLE, ""]

    parts += [
        "CONVERSATION STYLE:",
        "- This is SPOKEN dialogue, not text. Use natural spoken English with contractions.",
        "- Keep each turn SHORT: 1-3 sentences. Never lecture.",
        "- Ask ONE question at a time. Wait for the answer before moving on.",
        "- Sound warm, professional, and human. Not robotic.",
        "- If the customer goes off-topic, gently bring them back.",
        "- If they get emotional or upset, acknowledge their feelings first.",
        "- Pronounce currency as 'rupees' (e.g. 'twelve thousand four hundred eighty rupees').",
        "- Pronounce dates naturally (e.g. 'May twenty-eighth').",
        "- Do NOT use markdown, bullets, or bracketed stage directions.",
        "- Do NOT include your name in every line. You said it at the start; that's enough.",
        "- Do NOT push products. Do NOT upsell. This is a service call.",
        "",
        "Begin the call when prompted with the opening trigger.",
    ]
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Helpers: text post-processing for speech
# -----------------------------------------------------------------------------

def _clean_for_speech(text: str) -> str:
    text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\([^)]*pause[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"Rs\.\s*([\d,]+)", lambda m: _spell_rupees(m.group(1)), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _spell_rupees(amount_str: str) -> str:
    digits = amount_str.replace(",", "")
    if not digits.isdigit():
        return f"{amount_str} rupees"
    n = int(digits)
    if n < 1_000:
        return f"{n} rupees"
    if n < 100_000:
        return f"{n:,} rupees"
    if n < 10_000_000:
        lakhs = n / 100_000
        return f"{lakhs:.1f} lakh rupees" if lakhs != int(lakhs) else f"{int(lakhs)} lakh rupees"
    crores = n / 10_000_000
    return f"{crores:.2f} crore rupees"


async def _split_into_sentences(text_iter: AsyncIterator[str]) -> AsyncIterator[str]:
    """Group a token stream into sentence-sized chunks."""
    buf = ""
    async for token in text_iter:
        buf += token
        while True:
            m = _SENTENCE_END.search(buf)
            if not m:
                break
            end = m.end()
            sentence = buf[:end].strip()
            buf = buf[end:]
            if sentence:
                yield _clean_for_speech(sentence)
    if buf.strip():
        yield _clean_for_speech(buf.strip())


# -----------------------------------------------------------------------------
# Backend: Ollama (local Qwen2.5)
# -----------------------------------------------------------------------------

def _ollama_messages(session, scenario_id: str) -> list[dict]:
    """Build the messages list for the next ollama.chat call from session history."""
    if not session.history:
        session.history.append({"role": "system", "content": build_system_prompt(scenario_id)})
    return session.history


async def _ollama_generate_opening(session, scenario_id: str) -> str:
    import ollama
    msgs = _ollama_messages(session, scenario_id) + [{"role": "user", "content": _OPENING_TRIGGER}]

    def _run():
        resp = ollama.chat(
            model=OLLAMA_MODEL,
            messages=msgs,
            options={"temperature": 0.6, "num_predict": 120},
        )
        return resp

    resp = await asyncio.to_thread(_run)
    session.add_tokens(
        int(resp.get("prompt_eval_count", 0) or 0),
        int(resp.get("eval_count", 0) or 0),
        model=OLLAMA_MODEL,
    )
    return _clean_for_speech(resp["message"]["content"].strip())


async def _ollama_reply_stream(session, scenario_id: str) -> AsyncIterator[str]:
    import ollama
    msgs = _ollama_messages(session, scenario_id)
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    usage: dict[str, int] = {"in": 0, "out": 0}

    def _producer():
        try:
            for chunk in ollama.chat(
                model=OLLAMA_MODEL,
                messages=msgs,
                stream=True,
                options={"temperature": 0.65, "num_predict": 200},
            ):
                token = chunk.get("message", {}).get("content", "")
                if token:
                    queue.put_nowait(token)
                # Final chunk carries usage counters.
                if chunk.get("done"):
                    usage["in"] = int(chunk.get("prompt_eval_count", 0) or 0)
                    usage["out"] = int(chunk.get("eval_count", 0) or 0)
        except Exception:
            logger.exception("Ollama streaming failed")
            queue.put_nowait("I'm sorry, I had a technical issue. Could you repeat that?")
        finally:
            queue.put_nowait(None)

    asyncio.get_running_loop().run_in_executor(None, _producer)

    async def _token_stream():
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item

    async for sentence in _split_into_sentences(_token_stream()):
        yield sentence

    if usage["in"] or usage["out"]:
        session.add_tokens(usage["in"], usage["out"], model=OLLAMA_MODEL)


# -----------------------------------------------------------------------------
# Backend: Claude (via claude-agent-sdk, reusing local Claude Code auth)
# -----------------------------------------------------------------------------

def _claude_options(scenario_id: str):
    from claude_agent_sdk import ClaudeAgentOptions
    return ClaudeAgentOptions(
        system_prompt=build_system_prompt(scenario_id),
        model=CLAUDE_MODEL,
        allowed_tools=[],
        permission_mode="default",
        max_turns=50,
        include_partial_messages=True,
    )


async def _claude_setup(session, scenario_id: str) -> None:
    from claude_agent_sdk import ClaudeSDKClient
    client = ClaudeSDKClient(options=_claude_options(scenario_id))
    await client.__aenter__()
    session.backend_state["claude_client"] = client
    logger.info("Claude client ready for session %s (model=%s)", session.session_id, CLAUDE_MODEL)


async def _claude_teardown(session) -> None:
    client = session.backend_state.pop("claude_client", None)
    if client is None:
        return
    try:
        await client.__aexit__(None, None, None)
    except Exception:
        logger.exception("Claude client teardown error")


async def _claude_token_stream(session, user_text: str) -> AsyncIterator[str]:
    """
    Yield text deltas from one Claude turn.

    Uses StreamEvent (when include_partial_messages=True) for true token-level
    streaming. Falls back to the final AssistantMessage if no deltas arrived.
    Also records authoritative token + USD cost numbers from the SDK's
    terminal ResultMessage (or, failing that, from the assistant's own usage
    block) into the session.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock, StreamEvent
    client = session.backend_state.get("claude_client")
    if client is None:
        raise RuntimeError("Claude client not initialised for this session")
    await client.query(user_text)

    streamed_any = False
    final_text = ""
    tokens_in = tokens_out = 0
    cost_usd: float | None = None
    async for message in client.receive_response():
        if isinstance(message, StreamEvent):
            ev = message.event or {}
            etype = ev.get("type")
            if etype == "content_block_delta":
                delta = ev.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        streamed_any = True
                        yield text
            elif etype == "message_delta":
                u = ev.get("usage") or {}
                tokens_out = int(u.get("output_tokens", tokens_out) or tokens_out)
            elif etype == "message_start":
                msg = ev.get("message", {})
                u = msg.get("usage") or {}
                tokens_in = int(u.get("input_tokens", tokens_in) or tokens_in)
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text:
                    final_text += block.text
        else:
            # ResultMessage (final): authoritative usage + cost numbers.
            usage = getattr(message, "usage", None)
            if isinstance(usage, dict):
                tokens_in = int(usage.get("input_tokens", tokens_in) or tokens_in)
                tokens_out = int(usage.get("output_tokens", tokens_out) or tokens_out)
            tc = getattr(message, "total_cost_usd", None)
            if tc is not None:
                cost_usd = float(tc)

    if not streamed_any and final_text:
        yield final_text

    if tokens_in or tokens_out or cost_usd is not None:
        session.add_tokens(tokens_in, tokens_out, model=CLAUDE_MODEL, cost_usd=cost_usd)


async def _claude_generate_opening(session, scenario_id: str) -> str:
    chunks: list[str] = []
    async for tok in _claude_token_stream(session, _OPENING_TRIGGER):
        chunks.append(tok)
    return _clean_for_speech("".join(chunks))


async def _claude_reply_stream(session, scenario_id: str) -> AsyncIterator[str]:
    user_text = ""
    for m in reversed(session.history):
        if m.get("role") == "user":
            user_text = m["content"]
            break
    if not user_text:
        return

    async for sentence in _split_into_sentences(_claude_token_stream(session, user_text)):
        yield sentence


# -----------------------------------------------------------------------------
# Outcome classifier (always one-shot, both backends)
# -----------------------------------------------------------------------------

OUTCOME_SCHEMA_PROMPT = """\
You will receive a transcript of a customer service phone call.
Summarise it as STRICT JSON. Output ONLY the JSON object, nothing else.

CRITICAL RULES:
1. NEVER infer facts not explicitly stated. If something was not said, use null / "info_only" / "call_incomplete".
2. Each "key_points" item MUST paraphrase something the customer or agent ACTUALLY said.
3. If the customer never made a clear decision, the outcome is "info_only" or "call_incomplete".
4. If the customer made 2 or fewer brief replies with no decision, use "call_incomplete".
5. customer_sentiment must reflect what the customer expressed. If unclear, use "neutral".

SCHEMA:
{
  "outcome": one of [
    "commitment"          // customer EXPLICITLY agreed to pay the full EMI by a specific date
    "partial_commitment"  // customer accepted a partial-payment / restructuring plan
    "refused"             // customer explicitly refused to pay
    "callback_requested"  // customer asked to be called back
    "dnd_requested"       // customer asked to stop being called
    "escalate"            // customer asked for a manager or threatened complaint
    "info_only"           // information exchanged but no decision
    "call_incomplete"     // call ended before any outcome could be determined
  ],
  "commitment_amount": integer rupees or null,
  "commitment_date": "YYYY-MM-DD" or null,
  "restructuring_option_accepted": "partial" | "extension" | "deferral" | null,
  "customer_sentiment": "positive" | "neutral" | "concerned" | "frustrated" | "angry",
  "key_points": [up to 4 short paraphrases of actual quotes],
  "next_action": one short sentence describing what an operations agent should do next
}"""


def _count_user_turns(history: list[dict]) -> int:
    return sum(1 for m in history if m.get("role") == "user")


def _user_word_count(history: list[dict]) -> int:
    return sum(len(str(m.get("content", "")).split()) for m in history if m.get("role") == "user")


def _format_transcript(history: list[dict]) -> str:
    lines = []
    for m in history:
        if m["role"] == "system":
            continue
        who = "Agent" if m["role"] == "assistant" else "Customer"
        lines.append(f"{who}: {m['content']}")
    return "\n".join(lines)


def _empty_outcome(user_turns: int, user_words: int) -> dict:
    return {
        "outcome": "call_incomplete",
        "commitment_amount": None,
        "commitment_date": None,
        "restructuring_option_accepted": None,
        "customer_sentiment": "neutral",
        "key_points": [
            f"Call ended after {user_turns} customer reply(s) / {user_words} word(s) - insufficient to determine outcome.",
        ],
        "next_action": "Schedule a callback at a time more convenient for the customer.",
    }


def _validate_outcome(parsed: dict) -> dict:
    valid = {
        "commitment", "partial_commitment", "refused", "callback_requested",
        "dnd_requested", "escalate", "info_only", "call_incomplete",
    }
    if parsed.get("outcome") not in valid:
        parsed["outcome"] = "info_only"
    return parsed


async def classify_outcome(session, scenario_id: str) -> dict:
    user_turns = _count_user_turns(session.history)
    user_words = _user_word_count(session.history)

    if user_turns < 2 or user_words < 6:
        return _empty_outcome(user_turns, user_words)

    transcript = _format_transcript(session.history)
    prompt = f"{OUTCOME_SCHEMA_PROMPT}\n\nTRANSCRIPT:\n{transcript}\n\nProduce the JSON now."

    if LLM_CLASSIFIER == "claude":
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
            opts = ClaudeAgentOptions(
                system_prompt="You are a strict JSON-only response generator.",
                model=CLAUDE_MODEL,
                allowed_tools=[],
                permission_mode="default",
                max_turns=1,
            )
            chunks: list[str] = []
            tokens_in = tokens_out = 0
            cost_usd: float | None = None
            async for message in query(prompt=prompt, options=opts):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            chunks.append(block.text)
                else:
                    usage = getattr(message, "usage", None)
                    if isinstance(usage, dict):
                        tokens_in = int(usage.get("input_tokens", tokens_in) or tokens_in)
                        tokens_out = int(usage.get("output_tokens", tokens_out) or tokens_out)
                    tc = getattr(message, "total_cost_usd", None)
                    if tc is not None:
                        cost_usd = float(tc)
            if tokens_in or tokens_out or cost_usd is not None:
                session.add_tokens(tokens_in, tokens_out, model=CLAUDE_MODEL, cost_usd=cost_usd)
            raw = "".join(chunks).strip()
            raw = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.M)
            return _validate_outcome(json.loads(raw))
        except Exception:
            logger.exception("Claude outcome classification failed")
            return _empty_outcome(user_turns, user_words)

    try:
        import ollama
        def _run():
            return ollama.chat(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": OUTCOME_SCHEMA_PROMPT},
                    {"role": "user", "content": f"TRANSCRIPT:\n{transcript}\n\nProduce the JSON now."},
                ],
                options={"temperature": 0.0, "num_predict": 400},
                format="json",
            )
        resp = await asyncio.to_thread(_run)
        session.add_tokens(
            int(resp.get("prompt_eval_count", 0) or 0),
            int(resp.get("eval_count", 0) or 0),
            model=OLLAMA_MODEL,
        )
        return _validate_outcome(json.loads(resp["message"]["content"].strip()))
    except Exception:
        logger.exception("Ollama outcome classification failed")
        return _empty_outcome(user_turns, user_words)


# -----------------------------------------------------------------------------
# Public dispatchers
# -----------------------------------------------------------------------------

def _session_backend(session) -> str:
    return (getattr(session, "backend", None) or LLM_BACKEND).lower()


async def setup_session(session, scenario_id: str) -> None:
    backend = _session_backend(session)
    if backend == "claude":
        await _claude_setup(session, scenario_id)
    else:
        if not session.history:
            session.history.append({"role": "system", "content": build_system_prompt(scenario_id)})


async def teardown_session(session) -> None:
    if _session_backend(session) == "claude":
        await _claude_teardown(session)


async def generate_opening(session, scenario_id: str) -> str:
    if _session_backend(session) == "claude":
        return await _claude_generate_opening(session, scenario_id)
    return await _ollama_generate_opening(session, scenario_id)


async def reply_stream(session, scenario_id: str) -> AsyncIterator[str]:
    if _session_backend(session) == "claude":
        async for sentence in _claude_reply_stream(session, scenario_id):
            yield sentence
    else:
        async for sentence in _ollama_reply_stream(session, scenario_id):
            yield sentence


def available_backends() -> list[dict]:
    """Backends the frontend can offer in its picker."""
    return [
        {"id": "claude", "label": "Claude Sonnet 4.5", "hint": "higher quality · ~4-5 s per turn"},
        {"id": "ollama", "label": "Qwen 2.5 (local)",  "hint": "fast · ~1 s per turn"},
    ]


logger.info("LLM defaults: voice=%s, classifier=%s", LLM_BACKEND, LLM_CLASSIFIER)
