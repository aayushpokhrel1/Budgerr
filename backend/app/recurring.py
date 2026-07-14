"""Pure, DB-free logic for detecting subscription-like recurring charges.

Kept separate from the router so it's unit-testable against plain lists of
transaction-like objects (anything with .amount, .is_betting, .merchant_name,
.date — see Transaction in app/models/transactions.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_
from statistics import median
from typing import Protocol


class TransactionLike(Protocol):
    amount: float
    is_betting: bool
    merchant_name: str | None
    date: date_


@dataclass
class RecurringCluster:
    merchant_name: str
    last_amount: float
    avg_amount: float
    occurrences: int
    first_date: date_
    last_date: date_
    median_interval_days: float
    active: bool
    monthly_estimate: float


def _cluster_by_amount(txns: list[TransactionLike]) -> list[list[TransactionLike]]:
    """Greedily cluster transactions by amount similarity.

    Sort ascending by amount, then walk through building clusters: a
    transaction joins the current cluster if its amount is within 10% of the
    cluster's running median; otherwise it starts a new cluster. This is a
    simple approximation (not globally optimal), but sufficient for
    detecting distinct recurring amounts at a single merchant (e.g. a plan
    upgrade partway through history).
    """
    sorted_txns = sorted(txns, key=lambda t: float(t.amount))
    clusters: list[list[TransactionLike]] = []
    cluster_amounts: list[list[float]] = []

    for txn in sorted_txns:
        amount = float(txn.amount)
        placed = False
        if clusters:
            running_median = median(cluster_amounts[-1])
            if running_median > 0 and abs(amount - running_median) / running_median <= 0.10:
                clusters[-1].append(txn)
                cluster_amounts[-1].append(amount)
                placed = True
        if not placed:
            clusters.append([txn])
            cluster_amounts.append([amount])

    return clusters


def _median_gap_days(dates: list[date_]) -> float | None:
    """Median gap in days between consecutive distinct dates (dedup same-day)."""
    distinct_sorted = sorted(set(dates))
    if len(distinct_sorted) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(distinct_sorted, distinct_sorted[1:])]
    return median(gaps)


def detect_recurring_charges(
    transactions: list[TransactionLike], today: date_ | None = None
) -> list[RecurringCluster]:
    today = today or date_.today()

    eligible = [
        t
        for t in transactions
        if float(t.amount) > 0 and not t.is_betting and t.merchant_name is not None
    ]

    groups: dict[str, list[TransactionLike]] = {}
    for txn in eligible:
        key = txn.merchant_name.strip().lower()
        groups.setdefault(key, []).append(txn)

    results: list[RecurringCluster] = []

    for group_txns in groups.values():
        for cluster in _cluster_by_amount(group_txns):
            if len(cluster) < 3:
                continue

            dates = [t.date for t in cluster]
            gap = _median_gap_days(dates)
            if gap is None or not (20 <= gap <= 40):
                continue

            cluster_sorted_by_date = sorted(cluster, key=lambda t: t.date)
            most_recent = cluster_sorted_by_date[-1]
            first_date = cluster_sorted_by_date[0].date
            last_date = most_recent.date
            amounts = [float(t.amount) for t in cluster]
            avg_amount = round(sum(amounts) / len(amounts), 2)
            active = (today - last_date).days <= 45

            results.append(
                RecurringCluster(
                    merchant_name=most_recent.merchant_name,
                    last_amount=float(most_recent.amount),
                    avg_amount=avg_amount,
                    occurrences=len(cluster),
                    first_date=first_date,
                    last_date=last_date,
                    median_interval_days=round(gap, 1),
                    active=active,
                    monthly_estimate=avg_amount,
                )
            )

    results.sort(key=lambda c: c.monthly_estimate, reverse=True)
    return results
