"""Structured leg identity fields (game_id / player_id / market) on bet legs.

These carry the CLV join keys and the team-market settlement prerequisite.
Tests are DB-free: they exercise the exact conversion `create_bet` performs
(`BetLeg(**BetLegCreate(...).model_dump())`) and the settlement exclusion rule.
"""

from app.auto_settlement import _leg_result
from app.models.betting import BetLeg, BetStatus
from app.routers.bets import BetLegCreate


def test_team_leg_with_market_and_null_stat_type_is_not_settled():
    # Team legs carry `market` (not `stat_type`) and stay log-only: a null
    # stat_type must exclude the leg from auto-settlement.
    leg = BetLeg(
        player_name="Seattle Mariners @ Texas Rangers",
        stat_type=None,
        market="first_inning_runs",
        game_id=12345,
        line_value=0.5,
        side="under",
        odds=-120,
        leg_status=BetStatus.pending,
    )
    assert _leg_result(leg, {}) is None


def test_bet_leg_create_carries_structured_fields_into_model():
    # Mirrors create_bet's `BetLeg(**leg.model_dump())` (routers/bets.py).
    payload = BetLegCreate(
        player_name="Aaron Judge",
        stat_type="home_runs",
        game_id=777,
        player_id=592450,
        line_value=0.5,
        side="over",
        odds=150,
    )
    dumped = payload.model_dump()
    assert dumped["game_id"] == 777
    assert dumped["player_id"] == 592450
    assert dumped["market"] is None

    leg = BetLeg(**dumped)
    assert leg.game_id == 777
    assert leg.player_id == 592450
    assert leg.stat_type == "home_runs"
