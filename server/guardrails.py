"""
RBI fair-practice guardrails for the soft-collections bot.

Two layers:
1. PROMPT layer: system prompt instructs the LLM on the rules.
2. POST-GEN layer: defensive regex check on the LLM output before TTS.
   If a forbidden phrase appears we substitute a safe paraphrase.
"""

from __future__ import annotations
import re
from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

# Phrases the bot must never say (case-insensitive).
# We use word-boundary patterns to avoid false positives on partial matches.
FORBIDDEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\blegal action\b", re.I), "the appropriate next steps as per RBI guidelines"),
    (re.compile(r"\bpolice\b", re.I), "the appropriate authorities (only if required)"),
    (re.compile(r"\bjail\b", re.I), ""),
    (re.compile(r"\barrest(ed)?\b", re.I), ""),
    (re.compile(r"\bshame(ful)?\b", re.I), "concerning"),
    (re.compile(r"\bdefaulter\b", re.I), "customer with an overdue EMI"),
    (re.compile(r"\bblack-?list(ed)?\b", re.I), "marked as overdue in our records"),
    (re.compile(r"\bruin your (credit|life)\b", re.I), "impact your credit score"),
    (re.compile(r"\bwe will (call|contact) your (family|relatives|employer|boss|neighbour|neighbor)s?\b", re.I),
     "we will continue to reach you directly"),
    (re.compile(r"\b(threat|threaten|warning you)\b", re.I), "informing you"),
    (re.compile(r"\byou (better|must) pay (now|immediately|today)\b", re.I),
     "we'd appreciate your earliest payment"),
    (re.compile(r"\bdon'?t waste my time\b", re.I), ""),
]


# Banned tokens that should hard-block the response entirely (extreme cases).
HARD_BLOCK_PATTERNS = [
    re.compile(r"\b(kill|hurt|beat|hit) you\b", re.I),
    re.compile(r"\bf+u+c+k+", re.I),
]


def is_within_call_window(now: datetime | None = None) -> tuple[bool, str]:
    """RBI fair-practice: collections calls only between 8 AM and 7 PM IST."""
    now = now or datetime.now(IST)
    hour = now.hour
    if 8 <= hour < 19:
        return True, ""
    return False, (
        f"As per RBI fair-practice guidelines, collections calls are only made "
        f"between 8 AM and 7 PM. The current time is {now.strftime('%I:%M %p')} IST. "
        f"Please reach us at 1800-XXX-XXXX during business hours."
    )


def sanitize_agent_utterance(text: str) -> tuple[str, list[str]]:
    """
    Run defensive substitution on an LLM-generated agent utterance.

    Returns (cleaned_text, list_of_violations_logged).
    """
    violations: list[str] = []

    for pat in HARD_BLOCK_PATTERNS:
        if pat.search(text):
            violations.append(f"HARD_BLOCK:{pat.pattern}")
            return (
                "I apologise, let me rephrase. Could we please discuss how I can help "
                "you with your EMI payment today?",
                violations,
            )

    cleaned = text
    for pat, replacement in FORBIDDEN_PATTERNS:
        if pat.search(cleaned):
            violations.append(f"SUBST:{pat.pattern}")
            cleaned = pat.sub(replacement, cleaned)

    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, violations


COMPLIANCE_PREAMBLE = """\
You are bound by the following RBI Fair Practices Code rules. These are NON-NEGOTIABLE:

1. Identify yourself ("This is Priya from FlexiLoans") at the start of the call.
2. State the purpose of the call clearly.
3. Ask permission before discussing loan details ("Is this a good time?").
4. Speak only with the borrower. If anyone else picks up, end the call politely.
5. Use a calm, respectful, empathetic tone. Never raise your voice (in style).
6. NEVER threaten, shame, intimidate, or use abusive language.
7. NEVER mention legal action, police, jail, defaulter, blacklist, or contacting their family/employer/neighbours.
8. NEVER call before 8 AM or after 7 PM IST.
9. If the customer says "stop calling" or "don't call again", apologise, note the DND request, and END the call.
10. If asked about grievance redressal, share: 1800-XXX-XXXX, grievance@flexiloans.com, or RBI Sachet portal.
11. Always offer at least one restructuring option before considering escalation.
12. Never disclose loan details to a third party.
"""
