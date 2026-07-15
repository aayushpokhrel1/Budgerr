"""Extraction prompt + JSON parsing for sportsbook bet-slip screenshots.

Kept separate from the router so the JSON-parsing logic can be unit tested
against a canned model response, without calling the Anthropic API.
"""

import json
import re
from dataclasses import dataclass, field

SLIP_MODEL = "claude-sonnet-5"

KNOWN_STAT_TYPES = (
    "points",
    "rebounds",
    "assists",
    "hits",
    "total_bases",
    "home_runs",
    "rbis",
    "runs",
    "stolen_bases",
    "batter_strikeouts",
    "walks",
    "pitcher_strikeouts",
    "earned_runs",
    "hits_allowed",
    "walks_allowed",
    "outs_recorded",
)

EXTRACTION_PROMPT = (
    "You are looking at a screenshot of a sportsbook bet slip. Extract the "
    "following details:\n"
    "- sportsbook: the name of the sportsbook app/site shown, or null if unclear.\n"
    "- bet_type: \"single\" if there is one leg, \"parlay\" if there are multiple "
    "legs combined into one bet, or null if you can't tell.\n"
    "- stake: the amount wagered, as a number, or null if not shown.\n"
    "- potential_payout: the potential total payout (winnings + stake), as a "
    "number, or null if not shown.\n"
    "- legs: a list of every leg in the bet. For each leg, extract:\n"
    "  - player_name: the player's name, or null if this leg isn't player-specific.\n"
    "  - stat_type: the stat being bet on, in lowercase snake_case matching this "
    "list when possible: " + ", ".join(KNOWN_STAT_TYPES) + ". If none of these "
    "fit, use the literal stat text as shown (lowercased, snake_case).\n"
    "  - line_value: the numeric line (e.g. 24.5), or null if not shown.\n"
    "  - side: \"over\" or \"under\", or null if not shown.\n"
    "  - odds: the American odds as an integer (e.g. -110, +150), or null if "
    "not shown.\n"
    "- note: any caveat about what you couldn't read clearly, or null if none.\n\n"
    "Rules: NEVER guess numbers you cannot actually read in the image — use "
    "null instead. Return ONLY a JSON object with keys: sportsbook, bet_type, "
    "stake, potential_payout, legs, note. Do not include any explanation, "
    "markdown formatting, or code fences — just the raw JSON object."
)


@dataclass
class ParsedLeg:
    player_name: str | None = None
    stat_type: str | None = None
    line_value: float | None = None
    side: str | None = None
    odds: int | None = None


@dataclass
class ParsedSlip:
    sportsbook: str | None = None
    bet_type: str | None = None
    stake: float | None = None
    potential_payout: float | None = None
    legs: list[ParsedLeg] = field(default_factory=list)
    note: str | None = None


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = _FENCE_RE.sub("", text).strip()
    return text


def _extract_json_object(text: str) -> str:
    """Pull out the first top-level {...} JSON object from text, tolerating
    leading/trailing prose the model may add despite instructions."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _normalize_side(value) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in ("over", "under"):
        return lowered
    return None


def _normalize_bet_type(value) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in ("single", "parlay"):
        return lowered
    return None


def parse_slip_response(raw_text: str) -> ParsedSlip:
    """Parse the model's raw text response into a ParsedSlip.

    Robust to code fences and stray prose around the JSON object. Raises
    ValueError if no JSON object can be parsed at all.
    """
    cleaned = _strip_code_fences(raw_text)
    candidate = _extract_json_object(cleaned)

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON from model response: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Model response JSON was not an object")

    legs_raw = payload.get("legs") or []
    legs: list[ParsedLeg] = []
    if isinstance(legs_raw, list):
        for leg in legs_raw:
            if not isinstance(leg, dict):
                continue
            legs.append(
                ParsedLeg(
                    player_name=leg.get("player_name") or None,
                    stat_type=(leg.get("stat_type") or None),
                    line_value=_to_float(leg.get("line_value")),
                    side=_normalize_side(leg.get("side")),
                    odds=_to_int(leg.get("odds")),
                )
            )

    return ParsedSlip(
        sportsbook=payload.get("sportsbook") or None,
        bet_type=_normalize_bet_type(payload.get("bet_type")),
        stake=_to_float(payload.get("stake")),
        potential_payout=_to_float(payload.get("potential_payout")),
        legs=legs,
        note=payload.get("note") or None,
    )
