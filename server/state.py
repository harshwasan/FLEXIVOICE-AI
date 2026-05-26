"""Per-session conversation state."""

from __future__ import annotations
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


# Published USD per 1M tokens. Keep in one place so the UI cost number stays
# honest. Sonnet 4.5 is Anthropic's listed price; local models are free.
_PRICE_TABLE_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5":     {"in": 3.00, "out": 15.00},
    "claude-3-5-sonnet":     {"in": 3.00, "out": 15.00},
    "claude-3-5-haiku":      {"in": 0.80, "out":  4.00},
    "claude-3-opus":         {"in": 15.00, "out": 75.00},
}


def _estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Best-effort cost estimate for backends that don't report it directly."""
    if not model:
        return 0.0
    m = model.lower()
    for prefix, p in _PRICE_TABLE_USD_PER_MTOK.items():
        if m.startswith(prefix):
            return (tokens_in * p["in"] + tokens_out * p["out"]) / 1_000_000.0
    return 0.0  # local / unknown -> assume zero marginal cost


@dataclass
class Session:
    session_id: str
    scenario_id: str
    started_at: float
    history: list[dict] = field(default_factory=list)
    turn_count: int = 0
    guardrail_violations: list[str] = field(default_factory=list)
    latencies_ms: dict[str, list[float]] = field(default_factory=lambda: {"stt": [], "llm_first_sentence": [], "tts": [], "total_turn": []})
    outcome: dict | None = None
    backend: str = "claude"        # per-session LLM backend ("claude" | "ollama")
    # Backend-specific scratch (e.g. an active ClaudeSDKClient). Not serialised.
    backend_state: dict = field(default_factory=dict)
    # Token accounting (set by the LLM backends after each turn).
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    last_model: str = ""

    def add_system(self, text: str) -> None:
        self.history.append({"role": "system", "content": text})

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})
        self._log_turn("user", text)

    def add_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})
        self.turn_count += 1
        self._log_turn("assistant", text)

    def record_latency(self, stage: str, ms: float) -> None:
        if stage in self.latencies_ms:
            self.latencies_ms[stage].append(round(ms, 1))

    def add_tokens(
        self,
        tokens_in: int,
        tokens_out: int,
        model: str,
        cost_usd: float | None = None,
    ) -> None:
        """Record token usage from one LLM call.

        If `cost_usd` is provided we use it verbatim (Claude SDK gives an
        authoritative number including cache discounts). Otherwise we estimate
        from the model's published price table.
        """
        self.tokens_in += int(tokens_in or 0)
        self.tokens_out += int(tokens_out or 0)
        if cost_usd is None:
            cost_usd = _estimate_cost_usd(model, tokens_in, tokens_out)
        self.cost_usd += float(cost_usd or 0.0)
        self.last_model = model or self.last_model
        self._log_event("tokens", {
            "model": model,
            "in": int(tokens_in or 0),
            "out": int(tokens_out or 0),
            "cost_usd_delta": float(cost_usd or 0.0),
            "cost_usd_total": round(self.cost_usd, 6),
        })

    def record_violation(self, v: str) -> None:
        self.guardrail_violations.append(v)
        self._log_event("guardrail_violation", {"detail": v})

    def set_outcome(self, outcome: dict) -> None:
        self.outcome = outcome
        self._log_event("outcome", outcome)

    def _log_turn(self, role: str, text: str) -> None:
        self._write_jsonl({
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "turn",
            "role": role,
            "text": text,
        })

    def _log_event(self, event: str, payload: Any) -> None:
        self._write_jsonl({
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": event,
            "payload": payload,
        })

    def _write_jsonl(self, record: dict) -> None:
        path = LOG_DIR / f"{self.session_id}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to write session log")


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, scenario_id: str, backend: str = "claude") -> Session:
        sid = uuid.uuid4().hex[:12]
        sess = Session(session_id=sid, scenario_id=scenario_id, started_at=time.time(), backend=backend)
        self._sessions[sid] = sess
        sess._write_jsonl({
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "session_start",
            "scenario": scenario_id,
            "backend": backend,
            "session_id": sid,
        })
        return sess

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def close(self, sid: str) -> None:
        sess = self._sessions.get(sid)
        if not sess:
            return
        sess._write_jsonl({
            "ts": datetime.utcnow().isoformat() + "Z",
            "type": "session_end",
            "turns": sess.turn_count,
            "violations": len(sess.guardrail_violations),
        })


SESSIONS = SessionManager()
