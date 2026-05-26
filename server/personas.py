"""
Agent knowledge base.

Two scenarios, each with:
  - Customer profile (what the bot knows about the borrower)
  - Loan details
  - Scenario context (why the bot is calling)
  - Suggested user prompts (shown on the page so the judge knows what to try)

The LLM receives the SCENARIO config as part of its system prompt.
The FRONTEND receives the CUSTOMER + USER_HINTS to render the persona card.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any


@dataclass
class Customer:
    name: str
    loan_id: str
    phone_masked: str
    loan_amount: int
    tenure_months: int
    emi_amount: int
    interest_rate: float
    outstanding_principal: int
    next_emi_date: str
    last_payment_date: str
    occupation: str
    city: str


@dataclass
class RestructuringOption:
    id: str
    name: str
    description: str
    impact: str


@dataclass
class Scenario:
    id: str
    title: str
    short_description: str
    agent_objective: str
    customer: Customer
    dpd_days: int = 0
    restructuring_options: list[RestructuringOption] = field(default_factory=list)
    user_hints: list[str] = field(default_factory=list)
    knowledge_snippets: list[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Scenario 1: EMI Reminder (outbound, 3 days before due date)
# -----------------------------------------------------------------------------

EMI_REMINDER = Scenario(
    id="emi-reminder",
    title="Outbound EMI Reminder",
    short_description=(
        "Polite reminder call 3 days before EMI due date. "
        "Confirm payment readiness, offer help with autopay, "
        "capture a soft commitment."
    ),
    agent_objective=(
        "PRIMARY GOAL: confirm the customer is aware of, and on track for, the upcoming EMI. "
        "Keep the call SHORT (3-4 turns), warm, and respectful of their time.\n\n"
        "FLOW:\n"
        "1. Greet warmly. Identify yourself ('Priya from FlexiLoans') in one sentence.\n"
        "2. Briefly remind them: EMI of Rs.12,480 is due on 28-May-2026 (3 days away).\n"
        "3. Ask if they anticipate any difficulty paying on time.\n"
        "4. If FINE: thank them, confirm payment, wish them a good day, end the call.\n"
        "5. If they have CONCERNS: listen empathetically, ask what's going on, "
        "and acknowledge — do NOT promise restructuring here, that's a separate process.\n"
        "6. If they ask a question (statement, foreclosure, EMI date change, etc.), "
        "answer briefly using the reference knowledge below.\n\n"
        "IMPORTANT TONE RULES:\n"
        "- DO NOT upsell. Do NOT push auto-debit, top-up loans, or any product.\n"
        "- Only mention auto-debit if the customer ASKS about it, or if they say "
        "they're worried about forgetting in the future.\n"
        "- This is a reminder, not a sales call. Be brief and end gracefully."
    ),
    customer=Customer(
        name="Rajesh Kumar",
        loan_id="FLX-2024-08823",
        phone_masked="+91-98XXXX3210",
        loan_amount=250_000,
        tenure_months=24,
        emi_amount=12_480,
        interest_rate=14.5,
        outstanding_principal=187_200,
        next_emi_date="28-May-2026",
        last_payment_date="28-Apr-2026",
        occupation="Small business owner (kirana store)",
        city="Pune",
    ),
    user_hints=[
        "Try: 'Yes I will pay on time' -> bot thanks you and wraps up",
        "Try: 'I might be a few days late, business is slow' -> bot empathises, asks how late",
        "Try: 'Can you change my EMI date?' -> bot explains the process",
        "Try: 'What is my outstanding balance?' -> bot looks it up and tells you",
        "Try: 'I keep forgetting EMI dates' -> bot suggests auto-debit",
    ],
    knowledge_snippets=[
        "Auto-debit setup: customer can enable e-NACH via the FlexiLoans app under "
        "'Repayments -> Auto-debit'. Activation takes 1-2 working days.",
        "EMI date change: allowed once per year, must be requested at least 7 days "
        "before the current due date. New date must be between 1st and 10th of the month.",
        "Late payment: penalty of 2% per month on overdue amount + Rs.500 late fee. "
        "Reports to credit bureau after DPD-30.",
        "Foreclosure: allowed after 6 EMIs paid. Foreclosure charge = 4% of outstanding.",
    ],
)


# -----------------------------------------------------------------------------
# Scenario 2: Soft Collections (outbound, DPD-5)
# -----------------------------------------------------------------------------

SOFT_COLLECTIONS = Scenario(
    id="soft-collections",
    title="Outbound Soft Collections (DPD-5)",
    short_description=(
        "Empathetic call to a borrower 5 days past due date. "
        "Understand the situation, propose 1 of 3 restructuring options, "
        "capture next-payment commitment. Strictly RBI fair-practice compliant."
    ),
    agent_objective=(
        "1. Greet warmly. Identify yourself ('Priya from FlexiLoans') and the call's purpose.\n"
        "2. Ask permission to discuss the loan ('Is this a good time to talk?').\n"
        "3. If yes, gently note the EMI of Rs.22,850 was due on 20-May-2026 and is 5 days overdue.\n"
        "4. Ask open-ended: 'Is everything okay? Is there a reason for the delay?'\n"
        "5. LISTEN. Acknowledge their situation with empathy.\n"
        "6. Based on what they share, propose ONE of the 3 restructuring options that fits.\n"
        "7. If they accept, confirm the details and capture the commitment.\n"
        "8. If they refuse all options, offer a callback and end politely.\n"
        "9. NEVER threaten. NEVER raise voice. NEVER mention legal action. "
        "NEVER discuss the loan with anyone other than the borrower.\n"
        "10. Always offer the grievance redressal contact: 1800-XXX-XXXX, "
        "grievance@flexiloans.com if asked."
    ),
    dpd_days=5,
    customer=Customer(
        name="Anjali Sharma",
        loan_id="FLX-2024-05521",
        phone_masked="+91-91XXXX6789",
        loan_amount=400_000,
        tenure_months=36,
        emi_amount=22_850,
        interest_rate=15.2,
        outstanding_principal=312_400,
        next_emi_date="20-May-2026",
        last_payment_date="20-Apr-2026",
        occupation="Boutique owner",
        city="Indore",
    ),
    restructuring_options=[
        RestructuringOption(
            id="partial",
            name="Partial Payment Plan",
            description=(
                "Pay 50% (Rs.11,425) within 48 hours, "
                "balance Rs.11,425 added to next month's EMI."
            ),
            impact="No penalty, no credit-bureau report. Minor interest accrual of ~Rs.140.",
        ),
        RestructuringOption(
            id="extension",
            name="7-Day Due-Date Extension",
            description=(
                "Move the May-2026 EMI due date forward by 7 days "
                "(new date: 27-May-2026). Full EMI of Rs.22,850 paid on new date."
            ),
            impact="Late fee waived. No credit-bureau report. Interest of ~Rs.95 added.",
        ),
        RestructuringOption(
            id="deferral",
            name="1-Month EMI Deferral",
            description=(
                "Skip May-2026 EMI entirely. Tenure extended by 1 month. "
                "Customer resumes regular EMI of Rs.22,850 from June-2026."
            ),
            impact=(
                "Tenure extended by 1 month, total interest impact ~Rs.2,800 "
                "over loan life. Reported to bureau as 'restructured' (small ding)."
            ),
        ),
    ],
    user_hints=[
        "Try: 'Hello?' -> bot identifies itself and asks if it's a good time",
        "Try: 'My shop had a slow month, can't pay full EMI right now' -> bot empathises, proposes partial",
        "Try: 'I get my supplier payment on 27th, can you wait till then?' -> bot offers extension",
        "Try: 'Business is bad, I need 1 month break' -> bot offers deferral",
        "Try: 'Why are you calling me?' -> bot explains DPD and purpose calmly",
        "Try: 'I'll pay tomorrow' -> bot confirms commitment + sends reminder",
        "Try: 'Stop calling me!' -> bot apologises, notes DND request, offers callback option",
        "Try: 'Can I speak to your manager?' -> bot offers grievance contact",
    ],
    knowledge_snippets=[
        "DPD = Days Past Due. Anjali is 5 days past her 20-May-2026 due date.",
        "RBI Fair Practices Code: collections calls only between 8 AM and 7 PM. "
        "No threats, intimidation, or shaming. No disclosure to third parties.",
        "Customer can request DND (Do Not Disturb) at any time; honor immediately.",
        "Grievance: 1800-XXX-XXXX (toll-free), grievance@flexiloans.com, "
        "or RBI Sachet portal sachet.rbi.org.in if escalated.",
        "If customer requests, share that the next escalation step (after DPD-30) "
        "would be a credit-bureau report and a soft notice. Do NOT use this as a threat.",
        "Cooling-off: if customer requests to be called back, schedule for next business day.",
    ],
)


SCENARIOS: dict[str, Scenario] = {
    EMI_REMINDER.id: EMI_REMINDER,
    SOFT_COLLECTIONS.id: SOFT_COLLECTIONS,
}


def get_scenario(scenario_id: str) -> Scenario:
    if scenario_id not in SCENARIOS:
        raise KeyError(f"Unknown scenario: {scenario_id}")
    return SCENARIOS[scenario_id]


def scenario_public_payload(scenario_id: str) -> dict[str, Any]:
    """Payload returned to the frontend to render the persona card."""
    s = get_scenario(scenario_id)
    return {
        "id": s.id,
        "title": s.title,
        "short_description": s.short_description,
        "customer": asdict(s.customer),
        "dpd_days": s.dpd_days,
        "restructuring_options": [asdict(o) for o in s.restructuring_options],
        "user_hints": s.user_hints,
    }
