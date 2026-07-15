import base64

import anthropic
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.config import settings
from app.slip_parser import (
    EXTRACTION_PROMPT,
    SLIP_MODEL,
    ParsedSlip,
    parse_slip_response,
)

router = APIRouter(prefix="/bets", tags=["bet-import"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # ~10MB


class ParsedLegOut(BaseModel):
    player_name: str | None
    stat_type: str | None
    line_value: float | None
    side: str | None
    odds: int | None


class ParseSlipResponse(BaseModel):
    sportsbook: str | None
    bet_type: str | None
    stake: float | None
    potential_payout: float | None
    legs: list[ParsedLegOut]
    note: str | None


def _to_response(parsed: ParsedSlip) -> ParseSlipResponse:
    return ParseSlipResponse(
        sportsbook=parsed.sportsbook,
        bet_type=parsed.bet_type,
        stake=parsed.stake,
        potential_payout=parsed.potential_payout,
        legs=[
            ParsedLegOut(
                player_name=leg.player_name,
                stat_type=leg.stat_type,
                line_value=leg.line_value,
                side=leg.side,
                odds=leg.odds,
            )
            for leg in parsed.legs
        ],
        note=parsed.note,
    )


@router.post("/parse-slip", response_model=ParseSlipResponse)
async def parse_slip(file: UploadFile = File(...)) -> ParseSlipResponse:
    """Parse a sportsbook bet-slip screenshot into structured fields via the
    Claude API. Nothing is saved to the database — the response is meant to
    pre-fill the quick-entry bet form for a human to review and submit
    themselves.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400, detail=f"Expected an image upload, got {file.content_type!r}"
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large (max ~10MB)")
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=501,
            detail="ANTHROPIC_API_KEY is not configured; bet-slip parsing is unavailable",
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    image_b64 = base64.b64encode(raw_bytes).decode("ascii")

    try:
        response = client.messages.create(
            model=SLIP_MODEL,
            max_tokens=2048,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": file.content_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Slip parsing failed: {exc}") from exc

    raw_text = "".join(block.text for block in response.content if block.type == "text")

    try:
        parsed = parse_slip_response(raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Could not parse model response: {exc}") from exc

    return _to_response(parsed)
