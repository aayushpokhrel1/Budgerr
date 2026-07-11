SPORTSBOOK_MERCHANT_KEYWORDS = (
    "draftkings",
    "fanduel",
    "betmgm",
    "caesars sportsbook",
    "espn bet",
    "bet365",
    "pointsbet",
    "betrivers",
    "hard rock bet",
    "fanatics sportsbook",
)


def is_betting_merchant(merchant_name: str | None) -> bool:
    if not merchant_name:
        return False
    lowered = merchant_name.lower()
    return any(keyword in lowered for keyword in SPORTSBOOK_MERCHANT_KEYWORDS)
