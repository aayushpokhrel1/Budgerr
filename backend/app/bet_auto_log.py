"""Pure mapping logic for POST /bets/auto-log-recommendations.

Kept free of DB/HTTP concerns so the rec+edge -> Bet mapping can be unit
tested without a database or playstat running.
"""

from __future__ import annotations

from datetime import datetime, time, timezone

from app.models import Bet, BetLeg, BetStatus, BetType


def _edge_key(player_id, stat_type, side) -> tuple:
    return (player_id, stat_type, side)


def _index_edges(edges: list[dict]) -> dict[tuple, dict]:
    index: dict[tuple, dict] = {}
    for edge in edges:
        key = _edge_key(edge.get("player_id"), edge.get("stat_type"), edge.get("side"))
        # First match wins if there are duplicates.
        index.setdefault(key, edge)
    return index


def recommendations_to_bets(recs: list[dict], edges: list[dict]) -> list[Bet]:
    """Maps playstat parlay recommendations (+ edges for enrichment) to
    unsaved Bet objects. Does not check for existing external_ref — the
    caller is responsible for dedup against the DB.
    """
    edges_by_key = _index_edges(edges)
    bets: list[Bet] = []

    for rec in recs:
        parlay_id = rec.get("parlay_id")
        legs_data = rec.get("legs") or []
        combined_odds = rec.get("combined_odds") or 0

        bet_legs: list[BetLeg] = []
        placed_at = None
        for leg in legs_data:
            player_id = leg.get("player_id")
            stat_type = leg.get("stat_type")
            side = leg.get("side")
            edge = edges_by_key.get(_edge_key(player_id, stat_type, side))

            line_value = edge.get("line_value") if edge else None
            if placed_at is None and edge is not None and edge.get("date"):
                edge_date = edge["date"]
                # edge date may be a date string like "2026-07-17".
                if isinstance(edge_date, str):
                    year, month, day = (int(part) for part in edge_date.split("-")[:3])
                    placed_at = datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
                else:
                    placed_at = datetime.combine(edge_date, time(12, 0, 0), tzinfo=timezone.utc)

            bet_legs.append(
                BetLeg(
                    player_name=leg.get("player_name"),
                    stat_type=stat_type,
                    line_value=line_value,
                    side=side,
                    odds=leg.get("odds"),
                    model_prob=leg.get("model_prob"),
                    leg_status=BetStatus.pending,
                )
            )

        if placed_at is None:
            placed_at = datetime.now(timezone.utc)

        bet_type = BetType.parlay if len(bet_legs) > 1 else BetType.single

        bets.append(
            Bet(
                sportsbook="paper",
                bet_type=bet_type,
                stake=10,
                potential_payout=round(10 * combined_odds, 2),
                placed_at=placed_at,
                status=BetStatus.pending,
                is_paper=True,
                external_ref=f"playstat-parlay-{parlay_id}",
                legs=bet_legs,
            )
        )

    return bets
