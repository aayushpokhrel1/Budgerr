"""Pure aggregation logic for GET /bets/bankroll.

Kept free of DB/session concerns so it can be unit tested by building Bet
objects in memory.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from app.models import Bet, BetStatus


def compute_bankroll(bets: list[Bet], scope: Literal["real", "paper"]) -> dict:
    """Compute the /bets/bankroll payload over an already-scoped list of
    settled bets (settled_at is not null), ordered by settled_at.
    """
    ordered = sorted(bets, key=lambda b: b.settled_at)

    daily_net: dict = defaultdict(float)
    for bet in ordered:
        day = bet.settled_at.date()
        daily_net[day] += float(bet.net_result or 0)

    points = []
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for day in sorted(daily_net.keys()):
        net = daily_net[day]
        cumulative += net
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_drawdown = max(max_drawdown, drawdown)
        points.append(
            {
                "date": day.isoformat(),
                "net": round(net, 2),
                "cumulative": round(cumulative, 2),
            }
        )

    longest_losing_streak = 0
    current_streak = 0
    for bet in ordered:
        if bet.status == BetStatus.lost:
            current_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_streak)
        else:
            current_streak = 0

    return {
        "scope": scope,
        "points": points,
        "max_drawdown": round(max_drawdown, 2),
        "longest_losing_streak": longest_losing_streak,
    }
