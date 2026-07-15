import pytest

from app.slip_parser import parse_slip_response


def test_parse_clean_json_response():
    raw = """
    {
        "sportsbook": "DraftKings",
        "bet_type": "parlay",
        "stake": 10,
        "potential_payout": 45.5,
        "legs": [
            {
                "player_name": "Luka Doncic",
                "stat_type": "points",
                "line_value": 32.5,
                "side": "over",
                "odds": -115
            },
            {
                "player_name": "Aaron Judge",
                "stat_type": "home_runs",
                "line_value": 0.5,
                "side": "over",
                "odds": 150
            }
        ],
        "note": null
    }
    """
    parsed = parse_slip_response(raw)

    assert parsed.sportsbook == "DraftKings"
    assert parsed.bet_type == "parlay"
    assert parsed.stake == 10.0
    assert parsed.potential_payout == 45.5
    assert parsed.note is None
    assert len(parsed.legs) == 2

    leg1, leg2 = parsed.legs
    assert leg1.player_name == "Luka Doncic"
    assert leg1.stat_type == "points"
    assert leg1.line_value == 32.5
    assert leg1.side == "over"
    assert leg1.odds == -115

    assert leg2.player_name == "Aaron Judge"
    assert leg2.stat_type == "home_runs"
    assert leg2.odds == 150


def test_parse_fenced_json_response():
    raw = """```json
    {
        "sportsbook": "FanDuel",
        "bet_type": "single",
        "stake": 25,
        "potential_payout": 47.5,
        "legs": [
            {
                "player_name": "Shohei Ohtani",
                "stat_type": "total_bases",
                "line_value": 1.5,
                "side": "under",
                "odds": -130
            }
        ],
        "note": "Odds boost applied"
    }
    ```"""
    parsed = parse_slip_response(raw)

    assert parsed.sportsbook == "FanDuel"
    assert parsed.bet_type == "single"
    assert parsed.note == "Odds boost applied"
    assert len(parsed.legs) == 1
    assert parsed.legs[0].side == "under"


def test_parse_partial_null_fields():
    raw = """
    {
        "sportsbook": null,
        "bet_type": null,
        "stake": null,
        "potential_payout": null,
        "legs": [
            {
                "player_name": "Mystery Player",
                "stat_type": null,
                "line_value": null,
                "side": null,
                "odds": null
            }
        ],
        "note": "Screenshot was cropped, could not read stake or line"
    }
    """
    parsed = parse_slip_response(raw)

    assert parsed.sportsbook is None
    assert parsed.bet_type is None
    assert parsed.stake is None
    assert parsed.potential_payout is None
    assert len(parsed.legs) == 1

    leg = parsed.legs[0]
    assert leg.player_name == "Mystery Player"
    assert leg.stat_type is None
    assert leg.line_value is None
    assert leg.side is None
    assert leg.odds is None
    assert "cropped" in parsed.note


def test_parse_garbage_raises_value_error():
    with pytest.raises(ValueError):
        parse_slip_response("Sorry, I can't read this image clearly at all.")


def test_parse_invalid_bet_type_and_side_become_none():
    raw = """
    {
        "sportsbook": "BetMGM",
        "bet_type": "teaser",
        "stake": 5,
        "potential_payout": 10,
        "legs": [
            {
                "player_name": "Some Player",
                "stat_type": "rebounds",
                "line_value": 7.5,
                "side": "sideways",
                "odds": -110
            }
        ],
        "note": null
    }
    """
    parsed = parse_slip_response(raw)

    # Unrecognized enum-like values are normalized to None rather than raising.
    assert parsed.bet_type is None
    assert parsed.legs[0].side is None
