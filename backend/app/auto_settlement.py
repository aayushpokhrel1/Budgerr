from datetime import date, datetime, timezone

import httpx
from sqlalchemy.orm import Session, selectinload

from app.models import Bet, BetLeg, BetStatus
from app.playstat_client import get_box_scores

PLAYER_STAT_FIELDS = {"points", "rebounds", "assists"}


def _leg_result(leg: BetLeg, box_scores_by_player: dict[str, dict]) -> BetStatus | None:
    """Returns the leg's outcome if it can be resolved from today's box scores,
    or None if it can't (game not final yet, unrecognized stat/side, name
    mismatch) — those legs are simply left pending for the next run.
    """
    if not leg.player_name or not leg.stat_type or leg.line_value is None or not leg.side:
        return None
    stat_type = leg.stat_type.strip().lower()
    if stat_type not in PLAYER_STAT_FIELDS:
        return None
    side = leg.side.strip().lower()
    if side not in ("over", "under"):
        return None

    box = box_scores_by_player.get(leg.player_name.strip().lower())
    if box is None:
        return None
    actual = box.get(stat_type)
    if actual is None:
        return None

    line = float(leg.line_value)
    if actual == line:
        return BetStatus.push
    if side == "over":
        return BetStatus.won if actual > line else BetStatus.lost
    return BetStatus.won if actual < line else BetStatus.lost


def _bet_result(leg_statuses: list[BetStatus]) -> BetStatus:
    if any(status == BetStatus.lost for status in leg_statuses):
        return BetStatus.lost
    if all(status == BetStatus.push for status in leg_statuses):
        return BetStatus.push
    return BetStatus.won


def _net_result(bet: Bet, status: BetStatus) -> float:
    if status == BetStatus.won:
        return float(bet.potential_payout) - float(bet.stake)
    if status == BetStatus.push:
        return 0.0
    return -float(bet.stake)


def auto_settle_pending_bets(db: Session) -> list[int]:
    """Cross-references pending bets' legs against playstat's finalized box
    scores (matched on the bet's placed_at date) and settles whichever bets
    have every leg resolvable. Bets with unresolvable legs — game not final
    yet, non-prop bets with no player_name/stat_type, name mismatches — are
    left pending for the next run.
    """
    pending_bets = (
        db.query(Bet)
        .options(selectinload(Bet.legs))
        .filter(Bet.status == BetStatus.pending)
        .all()
    )
    if not pending_bets:
        return []

    box_scores_cache: dict[date, dict[str, dict]] = {}
    settled_bet_ids: list[int] = []

    for bet in pending_bets:
        game_date = bet.placed_at.date()
        if game_date not in box_scores_cache:
            try:
                scores = get_box_scores(game_date)
            except httpx.HTTPError:
                scores = []
            box_scores_cache[game_date] = {
                row["player_name"].strip().lower(): row for row in scores
            }
        box_scores_by_player = box_scores_cache[game_date]

        leg_statuses = [_leg_result(leg, box_scores_by_player) for leg in bet.legs]
        if not leg_statuses or any(status is None for status in leg_statuses):
            continue

        for leg, status in zip(bet.legs, leg_statuses):
            leg.leg_status = status

        bet_status = _bet_result(leg_statuses)
        bet.status = bet_status
        bet.net_result = round(_net_result(bet, bet_status), 2)
        bet.settled_at = datetime.now(timezone.utc)
        settled_bet_ids.append(bet.bet_id)

    db.commit()
    return settled_bet_ids
