from app.auto_settlement import _bet_result, _leg_result
from app.models import BetLeg, BetStatus


def leg(**kwargs) -> BetLeg:
    defaults = dict(player_name="Test Player", stat_type="points", line_value=20.5, side="over")
    defaults.update(kwargs)
    return BetLeg(**defaults)


NBA_BOX = {"test player": {"points": 25, "rebounds": 8, "assists": 4, "stats": None, "sport": "nba"}}
MLB_BOX = {
    "test player": {
        "points": None,
        "rebounds": None,
        "assists": None,
        "stats": {"hits": 2.0, "total_bases": 5.0, "home_runs": 1.0, "batter_strikeouts": 0.0},
        "sport": "mlb",
    }
}


class TestLegResult:
    def test_nba_leg_resolves_via_legacy_fields(self):
        assert _leg_result(leg(stat_type="points", line_value=20.5, side="over"), NBA_BOX) == BetStatus.won
        assert _leg_result(leg(stat_type="rebounds", line_value=9.5, side="over"), NBA_BOX) == BetStatus.lost

    def test_mlb_leg_resolves_via_stats_map(self):
        assert _leg_result(leg(stat_type="total_bases", line_value=1.5, side="over"), MLB_BOX) == BetStatus.won
        assert _leg_result(leg(stat_type="hits", line_value=2.5, side="over"), MLB_BOX) == BetStatus.lost

    def test_zero_valued_stat_still_resolves(self):
        assert _leg_result(leg(stat_type="batter_strikeouts", line_value=0.5, side="under"), MLB_BOX) == BetStatus.won

    def test_unknown_stat_stays_pending(self):
        assert _leg_result(leg(stat_type="pitcher_strikeouts"), MLB_BOX) is None
        assert _leg_result(leg(stat_type="blocks"), NBA_BOX) is None

    def test_push_on_exact_line(self):
        assert _leg_result(leg(stat_type="hits", line_value=2.0, side="over"), MLB_BOX) == BetStatus.push

    def test_under_side(self):
        assert _leg_result(leg(stat_type="points", line_value=30.5, side="under"), NBA_BOX) == BetStatus.won
        assert _leg_result(leg(stat_type="points", line_value=20.5, side="under"), NBA_BOX) == BetStatus.lost

    def test_unknown_player_stays_pending(self):
        assert _leg_result(leg(player_name="Nobody"), NBA_BOX) is None

    def test_incomplete_leg_stays_pending(self):
        assert _leg_result(leg(player_name=None), NBA_BOX) is None
        assert _leg_result(leg(side=None), NBA_BOX) is None
        assert _leg_result(leg(line_value=None), NBA_BOX) is None
        assert _leg_result(leg(side="exactly"), NBA_BOX) is None


class TestBetResult:
    def test_any_loss_loses_the_bet(self):
        assert _bet_result([BetStatus.won, BetStatus.lost, BetStatus.push]) == BetStatus.lost

    def test_all_push_pushes(self):
        assert _bet_result([BetStatus.push, BetStatus.push]) == BetStatus.push

    def test_wins_and_pushes_win(self):
        assert _bet_result([BetStatus.won, BetStatus.push]) == BetStatus.won
        assert _bet_result([BetStatus.won]) == BetStatus.won
