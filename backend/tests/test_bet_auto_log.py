from datetime import datetime, timezone

from app.bet_auto_log import recommendations_to_bets
from app.models import BetStatus, BetType


def make_rec(parlay_id=1, combined_odds=6.5, legs=None):
    return {
        "parlay_id": parlay_id,
        "created_at": "2026-07-14T10:00:00Z",
        "target_payout": 65.0,
        "joint_prob": 0.15,
        "combined_odds": combined_odds,
        "legs": legs or [],
    }


def make_leg(
    player_id=1,
    player_name="Test Player",
    game_id=100,
    stat_type="hits",
    side="over",
    model_prob=0.6,
    odds=-110,
):
    return {
        "player_id": player_id,
        "player_name": player_name,
        "game_id": game_id,
        "stat_type": stat_type,
        "side": side,
        "model_prob": model_prob,
        "odds": odds,
    }


def make_edge(
    player_id=1,
    player_name="Test Player",
    game_id=100,
    date="2026-07-17",
    stat_type="hits",
    side="over",
    line_value=1.5,
    odds=-110,
    model_prob=0.6,
    edge=0.05,
):
    return {
        "player_id": player_id,
        "player_name": player_name,
        "game_id": game_id,
        "date": date,
        "stat_type": stat_type,
        "side": side,
        "line_value": line_value,
        "odds": odds,
        "model_prob": model_prob,
        "edge": edge,
    }


class TestDedupeKeyFormat:
    def test_external_ref_format(self):
        rec = make_rec(parlay_id=42, legs=[make_leg()])
        bets = recommendations_to_bets([rec], [make_edge()])
        assert bets[0].external_ref == "playstat-parlay-42"


class TestEdgeEnrichment:
    def test_matched_edge_sets_line_value_and_placed_at(self):
        rec = make_rec(legs=[make_leg(player_id=7, stat_type="hits", side="over")])
        edge = make_edge(player_id=7, stat_type="hits", side="over", line_value=2.5, date="2026-07-17")
        bets = recommendations_to_bets([rec], [edge])
        bet = bets[0]
        assert bet.legs[0].line_value == 2.5
        assert bet.placed_at == datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)

    def test_unmatched_edge_line_none_and_placed_at_fallback(self):
        rec = make_rec(legs=[make_leg(player_id=99, stat_type="hits", side="over")])
        edge = make_edge(player_id=1, stat_type="hits", side="over")  # different player, no match
        before = datetime.now(timezone.utc)
        bets = recommendations_to_bets([rec], [edge])
        after = datetime.now(timezone.utc)
        bet = bets[0]
        assert bet.legs[0].line_value is None
        assert bet.legs[0].leg_status == BetStatus.pending
        assert before <= bet.placed_at <= after


class TestBetTypeAndFields:
    def test_single_leg_is_single_bet_type(self):
        rec = make_rec(legs=[make_leg()])
        bets = recommendations_to_bets([rec], [make_edge()])
        assert bets[0].bet_type == BetType.single
        assert len(bets[0].legs) == 1

    def test_multi_leg_is_parlay_bet_type(self):
        rec = make_rec(
            legs=[
                make_leg(player_id=1, stat_type="hits"),
                make_leg(player_id=2, stat_type="runs"),
            ]
        )
        bets = recommendations_to_bets([rec], [])
        assert bets[0].bet_type == BetType.parlay
        assert len(bets[0].legs) == 2

    def test_stake_and_payout_and_paper_flags(self):
        rec = make_rec(combined_odds=5.0, legs=[make_leg()])
        bets = recommendations_to_bets([rec], [])
        bet = bets[0]
        assert bet.sportsbook == "paper"
        assert bet.is_paper is True
        assert bet.stake == 10
        assert bet.potential_payout == 50.0
        assert bet.status == BetStatus.pending

    def test_leg_fields_from_rec(self):
        rec = make_rec(legs=[make_leg(player_name="Foo Bar", stat_type="runs", side="under", odds=120, model_prob=0.42)])
        bets = recommendations_to_bets([rec], [])
        leg = bets[0].legs[0]
        assert leg.player_name == "Foo Bar"
        assert leg.stat_type == "runs"
        assert leg.side == "under"
        assert leg.odds == 120
        assert leg.model_prob == 0.42


class TestMultipleRecommendations:
    def test_produces_one_bet_per_recommendation(self):
        recs = [make_rec(parlay_id=1, legs=[make_leg()]), make_rec(parlay_id=2, legs=[make_leg()])]
        bets = recommendations_to_bets(recs, [])
        assert len(bets) == 2
        assert {b.external_ref for b in bets} == {"playstat-parlay-1", "playstat-parlay-2"}

    def test_empty_recommendations(self):
        assert recommendations_to_bets([], []) == []
