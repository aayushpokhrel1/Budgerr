from datetime import datetime, timezone

from app.bet_analytics import compute_analytics
from app.models import Bet, BetLeg, BetStatus, BetType


def make_bet(
    sportsbook="draftkings",
    bet_type=BetType.single,
    stake=100.0,
    net_result=0.0,
    status=BetStatus.won,
    settled=True,
    is_paper=False,
    legs=None,
) -> Bet:
    return Bet(
        sportsbook=sportsbook,
        bet_type=bet_type,
        stake=stake,
        potential_payout=stake * 2,
        placed_at=datetime.now(timezone.utc),
        status=status,
        settled_at=datetime.now(timezone.utc) if settled else None,
        net_result=net_result,
        is_paper=is_paper,
        legs=legs or [],
    )


def make_leg(stat_type="hits", leg_status=BetStatus.won, model_prob=None) -> BetLeg:
    return BetLeg(
        player_name="Test Player",
        stat_type=stat_type,
        line_value=1.5,
        side="over",
        odds=-110,
        model_prob=model_prob,
        leg_status=leg_status,
    )


class TestEmptyInput:
    def test_empty_list(self):
        result = compute_analytics([], "real")
        assert result["scope"] == "real"
        assert result["overall"] == {
            "settled": 0,
            "wins": 0,
            "losses": 0,
            "pushes": 0,
            "total_staked": 0.0,
            "net_profit": 0.0,
            "roi": None,
        }
        assert result["by_sportsbook"] == []
        assert result["by_bet_type"] == []
        assert result["by_stat_type"] == []
        assert result["calibration"] == {
            "legs": 0,
            "overall_predicted": None,
            "overall_actual": None,
            "buckets": [],
        }


class TestRoiMath:
    def test_roi_and_totals(self):
        bets = [
            make_bet(stake=100.0, net_result=50.0, status=BetStatus.won),
            make_bet(stake=100.0, net_result=-100.0, status=BetStatus.lost),
            make_bet(stake=50.0, net_result=0.0, status=BetStatus.push),
        ]
        result = compute_analytics(bets, "real")
        overall = result["overall"]
        assert overall["settled"] == 3
        assert overall["wins"] == 1
        assert overall["losses"] == 1
        assert overall["pushes"] == 1
        assert overall["total_staked"] == 250.0
        assert overall["net_profit"] == -50.0
        assert overall["roi"] == round(-50.0 / 250.0, 4)

    def test_zero_staked_roi_is_null(self):
        bets = [make_bet(stake=0.0, net_result=0.0, status=BetStatus.push)]
        result = compute_analytics(bets, "real")
        assert result["overall"]["roi"] is None

    def test_cashed_out_counts_in_settled_and_net_but_not_wlp(self):
        bets = [make_bet(net_result=25.0, status=BetStatus.cashed_out)]
        result = compute_analytics(bets, "real")
        overall = result["overall"]
        assert overall["settled"] == 1
        assert overall["wins"] == 0
        assert overall["losses"] == 0
        assert overall["pushes"] == 0
        assert overall["net_profit"] == 25.0


class TestGroupings:
    def test_by_sportsbook_and_bet_type_sorted_by_settled_desc(self):
        bets = [
            make_bet(sportsbook="draftkings", bet_type=BetType.single),
            make_bet(sportsbook="draftkings", bet_type=BetType.parlay),
            make_bet(sportsbook="fanduel", bet_type=BetType.single),
        ]
        result = compute_analytics(bets, "real")
        sportsbooks = {g["key"]: g for g in result["by_sportsbook"]}
        assert sportsbooks["draftkings"]["settled"] == 2
        assert sportsbooks["fanduel"]["settled"] == 1
        assert result["by_sportsbook"][0]["key"] == "draftkings"

        bet_types = {g["key"]: g for g in result["by_bet_type"]}
        assert bet_types["single"]["settled"] == 2
        assert bet_types["parlay"]["settled"] == 1


class TestStatTypeHitRate:
    def test_hit_rate_excludes_pushes_from_denominator(self):
        bets = [
            make_bet(
                legs=[
                    make_leg(stat_type="hits", leg_status=BetStatus.won),
                    make_leg(stat_type="hits", leg_status=BetStatus.lost),
                    make_leg(stat_type="hits", leg_status=BetStatus.push),
                ]
            )
        ]
        result = compute_analytics(bets, "real")
        hits = next(s for s in result["by_stat_type"] if s["key"] == "hits")
        assert hits["legs"] == 3
        assert hits["won"] == 1
        assert hits["lost"] == 1
        assert hits["pushed"] == 1
        assert hits["hit_rate"] == 0.5

    def test_all_push_hit_rate_is_null(self):
        bets = [make_bet(legs=[make_leg(stat_type="hits", leg_status=BetStatus.push)])]
        result = compute_analytics(bets, "real")
        hits = next(s for s in result["by_stat_type"] if s["key"] == "hits")
        assert hits["hit_rate"] is None

    def test_pending_and_null_stat_type_legs_excluded(self):
        bets = [
            make_bet(
                legs=[
                    make_leg(stat_type="hits", leg_status=BetStatus.pending),
                    make_leg(stat_type=None, leg_status=BetStatus.won),
                ]
            )
        ]
        result = compute_analytics(bets, "real")
        assert result["by_stat_type"] == []


class TestCalibration:
    def test_bucketing_and_push_exclusion(self):
        bets = [
            make_bet(
                legs=[
                    make_leg(stat_type="hits", leg_status=BetStatus.won, model_prob=0.55),
                    make_leg(stat_type="hits", leg_status=BetStatus.lost, model_prob=0.62),
                    make_leg(stat_type="hits", leg_status=BetStatus.push, model_prob=0.58),
                    make_leg(stat_type="hits", leg_status=BetStatus.won, model_prob=0.91),
                    make_leg(stat_type="hits", leg_status=None, model_prob=None),
                ]
            )
        ]
        result = compute_analytics(bets, "real")
        cal = result["calibration"]
        assert cal["legs"] == 3  # push and null-prob excluded
        bucket_5060 = next(b for b in cal["buckets"] if b["lo"] == 0.5)
        assert bucket_5060["legs"] == 1
        assert bucket_5060["predicted"] == 0.55
        assert bucket_5060["actual"] == 1.0
        bucket_6070 = next(b for b in cal["buckets"] if b["lo"] == 0.6)
        assert bucket_6070["legs"] == 1
        assert bucket_6070["predicted"] == 0.62
        assert bucket_6070["actual"] == 0.0
        bucket_90 = next(b for b in cal["buckets"] if b["lo"] == 0.9)
        assert bucket_90["legs"] == 1
        assert bucket_90["actual"] == 1.0
        assert cal["overall_predicted"] == round((0.55 + 0.62 + 0.91) / 3, 4)
        assert cal["overall_actual"] == round(2 / 3, 4)

    def test_only_non_empty_buckets_included(self):
        bets = [make_bet(legs=[make_leg(stat_type="hits", leg_status=BetStatus.won, model_prob=0.05)])]
        result = compute_analytics(bets, "real")
        assert len(result["calibration"]["buckets"]) == 1
        assert result["calibration"]["buckets"][0]["lo"] == 0.0
        assert result["calibration"]["buckets"][0]["hi"] == 0.1

    def test_prob_of_one_falls_in_last_bucket(self):
        bets = [make_bet(legs=[make_leg(stat_type="hits", leg_status=BetStatus.won, model_prob=1.0)])]
        result = compute_analytics(bets, "real")
        buckets = result["calibration"]["buckets"]
        assert len(buckets) == 1
        assert buckets[0]["lo"] == 0.9
        assert buckets[0]["hi"] == 1.0
