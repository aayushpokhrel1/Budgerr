from dataclasses import dataclass

import anthropic

from app.config import settings

LOOKUP_MODEL = "claude-sonnet-5"

RECORD_TOOL = {
    "name": "record_reward_rates",
    "description": "Record the credit card's current reward categories and rates, based on the research just done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category_name": {
                            "type": "string",
                            "description": (
                                "Short, generic spending category, e.g. 'Dining', 'Groceries', "
                                "'Travel', 'Gas stations', 'Streaming services'. Not a card-specific "
                                "program name."
                            ),
                        },
                        "multiplier": {
                            "type": "number",
                            "description": "Reward rate as a percent, e.g. 3 for 3x points or 3% cash back.",
                        },
                        "cap_amount": {
                            "type": ["number", "null"],
                            "description": "Dollar spending cap this rate applies up to per cap_period, or null if uncapped.",
                        },
                        "cap_period": {
                            "type": ["string", "null"],
                            "enum": ["quarterly", "annual", None],
                        },
                        "effective_end": {
                            "type": ["string", "null"],
                            "description": (
                                "ISO date (YYYY-MM-DD) this rate stops applying, for a rotating/"
                                "time-limited category. Null for a standing rate."
                            ),
                        },
                        "notes": {
                            "type": "string",
                            "description": "Brief caveat, e.g. 'requires quarterly activation'. Empty string if none.",
                        },
                    },
                    "required": ["category_name", "multiplier", "cap_amount", "cap_period", "effective_end", "notes"],
                },
            },
            "source_summary": {
                "type": "string",
                "description": "One sentence on where this came from and how current it is.",
            },
        },
        "required": ["rates", "source_summary"],
    },
}


@dataclass
class ProposedRate:
    category_name: str
    multiplier: float
    cap_amount: float | None
    cap_period: str | None
    effective_end: str | None
    notes: str


@dataclass
class RewardLookupResult:
    rates: list[ProposedRate]
    source_summary: str


def fetch_card_reward_rates(card_name: str, issuer: str) -> RewardLookupResult:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    research_prompt = (
        f"Research the current credit card rewards structure for the "
        f"{issuer} {card_name}. Find every spending category that earns an "
        f"elevated rewards rate (points multiplier or cash back percentage) "
        f"above the card's base rate, including any rotating or capped "
        f"categories, using up-to-date sources (the issuer's own terms page "
        f"if possible)."
    )
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": research_prompt}]

    research_response = client.messages.create(
        model=LOOKUP_MODEL,
        max_tokens=4096,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=messages,
    )
    messages.append({"role": "assistant", "content": research_response.content})
    messages.append(
        {
            "role": "user",
            "content": (
                "Now call record_reward_rates with the structured results of that research. "
                "Only include categories with an elevated rate above the card's base rate."
            ),
        }
    )

    record_response = client.messages.create(
        model=LOOKUP_MODEL,
        max_tokens=4096,
        tools=[RECORD_TOOL],
        tool_choice={"type": "tool", "name": "record_reward_rates"},
        messages=messages,
    )

    tool_use = next(block for block in record_response.content if block.type == "tool_use")
    payload = tool_use.input

    rates = [
        ProposedRate(
            category_name=r["category_name"],
            multiplier=float(r["multiplier"]),
            cap_amount=float(r["cap_amount"]) if r.get("cap_amount") is not None else None,
            cap_period=r.get("cap_period"),
            effective_end=r.get("effective_end") or None,
            notes=r.get("notes") or "",
        )
        for r in payload["rates"]
    ]
    return RewardLookupResult(rates=rates, source_summary=payload["source_summary"])
