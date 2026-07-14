from dataclasses import dataclass
from datetime import date, timedelta

from app.recurring import detect_recurring_charges


@dataclass
class FakeTxn:
    amount: float
    merchant_name: str | None
    date: date
    is_betting: bool = False


def _monthly_dates(start: date, count: int, step_days: int = 30) -> list[date]:
    return [start + timedelta(days=step_days * i) for i in range(count)]


def test_detects_monthly_subscription():
    dates = _monthly_dates(date(2026, 1, 15), 6)
    txns = [FakeTxn(amount=15.99, merchant_name="Netflix", date=d) for d in dates]

    clusters = detect_recurring_charges(txns, today=dates[-1])

    assert len(clusters) == 1
    c = clusters[0]
    assert c.merchant_name == "Netflix"
    assert c.occurrences == 6
    assert c.avg_amount == 15.99
    assert c.last_amount == 15.99
    assert 20 <= c.median_interval_days <= 40
    assert c.first_date == dates[0]
    assert c.last_date == dates[-1]
    assert c.monthly_estimate == 15.99


def test_rejects_fewer_than_three_occurrences():
    dates = _monthly_dates(date(2026, 1, 15), 2)
    txns = [FakeTxn(amount=9.99, merchant_name="Spotify", date=d) for d in dates]

    clusters = detect_recurring_charges(txns, today=dates[-1])

    assert clusters == []


def test_rejects_irregular_cadence():
    # Gaps of 1, 90, 3 days - nowhere near a monthly cadence.
    base = date(2026, 1, 1)
    dates = [base, base + timedelta(days=1), base + timedelta(days=91), base + timedelta(days=94)]
    txns = [FakeTxn(amount=20.0, merchant_name="Random Store", date=d) for d in dates]

    clusters = detect_recurring_charges(txns, today=dates[-1])

    assert clusters == []


def test_separates_two_amounts_at_same_merchant_into_clusters():
    dates_low = _monthly_dates(date(2026, 1, 1), 4)
    dates_high = _monthly_dates(date(2026, 1, 1), 4)
    txns = [FakeTxn(amount=10.0, merchant_name="Gym", date=d) for d in dates_low]
    txns += [FakeTxn(amount=50.0, merchant_name="Gym", date=d) for d in dates_high]

    clusters = detect_recurring_charges(txns, today=dates_high[-1])

    assert len(clusters) == 2
    amounts = sorted(c.avg_amount for c in clusters)
    assert amounts == [10.0, 50.0]


def test_tolerates_small_amount_drift():
    dates = _monthly_dates(date(2026, 1, 15), 5)
    amounts = [15.99, 16.20, 15.80, 16.00, 15.99]  # all within 10% of each other
    txns = [FakeTxn(amount=a, merchant_name="Hulu", date=d) for a, d in zip(amounts, dates)]

    clusters = detect_recurring_charges(txns, today=dates[-1])

    assert len(clusters) == 1
    assert clusters[0].occurrences == 5


def test_excludes_betting_and_non_positive_amounts():
    dates = _monthly_dates(date(2026, 1, 15), 4)
    txns = [FakeTxn(amount=20.0, merchant_name="DraftKings", date=d, is_betting=True) for d in dates]
    txns += [FakeTxn(amount=-20.0, merchant_name="Refund Co", date=d) for d in dates]
    txns += [FakeTxn(amount=20.0, merchant_name=None, date=d) for d in dates]

    clusters = detect_recurring_charges(txns, today=dates[-1])

    assert clusters == []


def test_active_flag_cutoff():
    today = date(2026, 7, 14)

    # Last charge 30 days ago -> active.
    recent_dates = _monthly_dates(today - timedelta(days=120), 4)
    recent_txns = [FakeTxn(amount=12.0, merchant_name="Active Co", date=d) for d in recent_dates]

    # Last charge 100 days ago -> inactive (well beyond 45 days).
    stale_dates = _monthly_dates(today - timedelta(days=220), 4)
    stale_txns = [FakeTxn(amount=8.0, merchant_name="Stale Co", date=d) for d in stale_dates]

    clusters = detect_recurring_charges(recent_txns + stale_txns, today=today)

    by_name = {c.merchant_name: c for c in clusters}
    assert (today - recent_dates[-1]).days <= 45
    assert by_name["Active Co"].active is True
    assert (today - stale_dates[-1]).days > 45
    assert by_name["Stale Co"].active is False


def test_monthly_total_sums_only_active():
    today = date(2026, 7, 14)

    active_dates = _monthly_dates(today - timedelta(days=90), 4)
    active_txns = [FakeTxn(amount=10.0, merchant_name="Active Sub", date=d) for d in active_dates]

    inactive_dates = _monthly_dates(today - timedelta(days=300), 4)
    inactive_txns = [FakeTxn(amount=25.0, merchant_name="Inactive Sub", date=d) for d in inactive_dates]

    clusters = detect_recurring_charges(active_txns + inactive_txns, today=today)

    monthly_total = round(sum(c.monthly_estimate for c in clusters if c.active), 2)
    assert monthly_total == 10.0
