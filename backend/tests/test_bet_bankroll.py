from datetime import datetime, timezone

from app.bet_bankroll import compute_bankroll
from app.models import Bet, BetStatus, BetType


def make_bet(net_result, status, settled_at, is_paper=False):
    return Bet(
        sportsbook="draftkings",
        bet_type=BetType.single,
        stake=10.0,
        potential_payout=20.0,
        placed_at=settled_at,
        status=status,
        settled_at=settled_at,
        net_result=net_result,
        is_paper=is_paper,
        legs=[],
    )


def dt(day):
    return datetime(2026, 7, day, 12, 0, 0, tzinfo=timezone.utc)


class TestEmpty:
    def test_empty_list(self):
        result = compute_bankroll([], "real")
        assert result == {
            "scope": "real",
            "points": [],
            "max_drawdown": 0.0,
            "longest_losing_streak": 0,
        }


class TestCumulative:
    def test_cumulative_sums_across_days(self):
        bets = [
            make_bet(10.0, BetStatus.won, dt(1)),
            make_bet(-5.0, BetStatus.lost, dt(2)),
            make_bet(20.0, BetStatus.won, dt(3)),
        ]
        result = compute_bankroll(bets, "real")
        assert result["points"] == [
            {"date": "2026-07-01", "net": 10.0, "cumulative": 10.0},
            {"date": "2026-07-02", "net": -5.0, "cumulative": 5.0},
            {"date": "2026-07-03", "net": 20.0, "cumulative": 25.0},
        ]

    def test_multiple_bets_same_day_are_summed(self):
        bets = [
            make_bet(10.0, BetStatus.won, dt(1)),
            make_bet(-3.0, BetStatus.lost, dt(1)),
        ]
        result = compute_bankroll(bets, "real")
        assert result["points"] == [{"date": "2026-07-01", "net": 7.0, "cumulative": 7.0}]


class TestDrawdown:
    def test_known_drawdown_series(self):
        # cumulative: 10, 30, 15, 5, 25 -> peak 30, trough 5 -> drawdown 25
        bets = [
            make_bet(10.0, BetStatus.won, dt(1)),
            make_bet(20.0, BetStatus.won, dt(2)),
            make_bet(-15.0, BetStatus.lost, dt(3)),
            make_bet(-10.0, BetStatus.lost, dt(4)),
            make_bet(20.0, BetStatus.won, dt(5)),
        ]
        result = compute_bankroll(bets, "real")
        assert result["max_drawdown"] == 25.0

    def test_never_down_gives_zero_drawdown(self):
        bets = [make_bet(10.0, BetStatus.won, dt(1)), make_bet(5.0, BetStatus.won, dt(2))]
        result = compute_bankroll(bets, "real")
        assert result["max_drawdown"] == 0.0


class TestLosingStreak:
    def test_longest_losing_streak_counts_consecutive_losses(self):
        bets = [
            make_bet(10.0, BetStatus.won, dt(1)),
            make_bet(-10.0, BetStatus.lost, dt(2)),
            make_bet(-10.0, BetStatus.lost, dt(3)),
            make_bet(-10.0, BetStatus.lost, dt(4)),
            make_bet(10.0, BetStatus.won, dt(5)),
            make_bet(-10.0, BetStatus.lost, dt(6)),
        ]
        result = compute_bankroll(bets, "real")
        assert result["longest_losing_streak"] == 3

    def test_push_breaks_streak(self):
        bets = [
            make_bet(-10.0, BetStatus.lost, dt(1)),
            make_bet(0.0, BetStatus.push, dt(2)),
            make_bet(-10.0, BetStatus.lost, dt(3)),
        ]
        result = compute_bankroll(bets, "real")
        assert result["longest_losing_streak"] == 1

    def test_no_losses(self):
        bets = [make_bet(10.0, BetStatus.won, dt(1))]
        result = compute_bankroll(bets, "real")
        assert result["longest_losing_streak"] == 0


class TestScope:
    def test_scope_is_passed_through(self):
        result = compute_bankroll([], "paper")
        assert result["scope"] == "paper"
