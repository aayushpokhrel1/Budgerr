"""Pure aggregation logic for GET /bets/analytics.

Kept free of DB/session concerns so it can be unit tested by building
Bet/BetLeg objects in memory.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal

from app.models import Bet, BetStatus


def _round_money(value: float) -> float:
    return round(value, 2)


def _round_rate(value: float) -> float:
    return round(value, 4)


def _group_stats(bets: Iterable[Bet]) -> dict:
    settled = 0
    wins = 0
    losses = 0
    pushes = 0
    total_staked = 0.0
    net_profit = 0.0
    for bet in bets:
        settled += 1
        if bet.status == BetStatus.won:
            wins += 1
        elif bet.status == BetStatus.lost:
            losses += 1
        elif bet.status == BetStatus.push:
            pushes += 1
        total_staked += float(bet.stake or 0)
        net_profit += float(bet.net_result or 0)

    roi = (net_profit / total_staked) if total_staked != 0 else None
    return {
        "settled": settled,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "total_staked": _round_money(total_staked),
        "net_profit": _round_money(net_profit),
        "roi": _round_rate(roi) if roi is not None else None,
    }


def _by_key(bets: list[Bet], key_fn) -> list[dict]:
    groups: dict[str, list[Bet]] = defaultdict(list)
    for bet in bets:
        groups[key_fn(bet)].append(bet)

    result = []
    for key, group_bets in groups.items():
        stats = _group_stats(group_bets)
        result.append({"key": key, **stats})

    result.sort(key=lambda g: g["settled"], reverse=True)
    return result


def _stat_type_stats(bets: list[Bet]) -> list[dict]:
    groups: dict[str, dict[str, int]] = defaultdict(lambda: {"legs": 0, "won": 0, "lost": 0, "pushed": 0})
    for bet in bets:
        for leg in bet.legs:
            if leg.stat_type is None:
                continue
            if leg.leg_status not in (BetStatus.won, BetStatus.lost, BetStatus.push):
                continue
            g = groups[leg.stat_type]
            g["legs"] += 1
            if leg.leg_status == BetStatus.won:
                g["won"] += 1
            elif leg.leg_status == BetStatus.lost:
                g["lost"] += 1
            elif leg.leg_status == BetStatus.push:
                g["pushed"] += 1

    result = []
    for key, g in groups.items():
        denom = g["won"] + g["lost"]
        hit_rate = (g["won"] / denom) if denom > 0 else None
        result.append(
            {
                "key": key,
                "legs": g["legs"],
                "won": g["won"],
                "lost": g["lost"],
                "pushed": g["pushed"],
                "hit_rate": _round_rate(hit_rate) if hit_rate is not None else None,
            }
        )

    result.sort(key=lambda g: g["legs"], reverse=True)
    return result


def _calibration(bets: list[Bet]) -> dict:
    eligible = []
    for bet in bets:
        for leg in bet.legs:
            if leg.model_prob is None:
                continue
            if leg.leg_status not in (BetStatus.won, BetStatus.lost):
                continue
            eligible.append(leg)

    legs_count = len(eligible)
    if legs_count == 0:
        return {
            "legs": 0,
            "overall_predicted": None,
            "overall_actual": None,
            "buckets": [],
        }

    overall_predicted = sum(leg.model_prob for leg in eligible) / legs_count
    overall_wins = sum(1 for leg in eligible if leg.leg_status == BetStatus.won)
    overall_actual = overall_wins / legs_count

    # Deciles: [0.0, 0.1), ..., [0.8, 0.9), [0.9, 1.0]
    bucket_data: dict[int, dict] = defaultdict(lambda: {"legs": 0, "prob_sum": 0.0, "won": 0})
    for leg in eligible:
        p = leg.model_prob
        idx = int(p * 10)
        if idx > 9:
            idx = 9
        if idx < 0:
            idx = 0
        b = bucket_data[idx]
        b["legs"] += 1
        b["prob_sum"] += p
        if leg.leg_status == BetStatus.won:
            b["won"] += 1

    buckets = []
    for idx in sorted(bucket_data.keys()):
        b = bucket_data[idx]
        lo = idx / 10
        hi = (idx + 1) / 10
        predicted = b["prob_sum"] / b["legs"]
        actual = b["won"] / b["legs"]
        buckets.append(
            {
                "lo": lo,
                "hi": hi,
                "legs": b["legs"],
                "predicted": _round_rate(predicted),
                "actual": _round_rate(actual),
            }
        )

    return {
        "legs": legs_count,
        "overall_predicted": _round_rate(overall_predicted),
        "overall_actual": _round_rate(overall_actual),
        "buckets": buckets,
    }


def compute_analytics(bets: list[Bet], scope: Literal["real", "paper"]) -> dict:
    """Compute the /bets/analytics payload over an already-scoped list of bets.

    `bets` should already be filtered to settled bets (settled_at is not
    null) and the requested is_paper scope by the caller. This function is
    pure and DB-free so it can be exercised directly in tests.
    """
    overall = _group_stats(bets)
    by_sportsbook = _by_key(bets, lambda b: b.sportsbook)
    by_bet_type = _by_key(bets, lambda b: b.bet_type.value if hasattr(b.bet_type, "value") else b.bet_type)
    by_stat_type = _stat_type_stats(bets)
    calibration = _calibration(bets)

    return {
        "scope": scope,
        "overall": overall,
        "by_sportsbook": by_sportsbook,
        "by_bet_type": by_bet_type,
        "by_stat_type": by_stat_type,
        "calibration": calibration,
    }
