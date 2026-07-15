from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, selectinload

from app.auto_settlement import auto_settle_pending_bets
from app.bet_analytics import compute_analytics
from app.bet_auto_log import recommendations_to_bets
from app.bet_bankroll import compute_bankroll
from app.deps import get_db
from app.models import Bet, BetLeg, BetStatus, BetType, Transaction
from app.playstat_client import get_edges, get_parlay_recommendations

router = APIRouter(prefix="/bets", tags=["bets"])


class BetLegCreate(BaseModel):
    player_name: str | None = None
    stat_type: str | None = None
    line_value: float | None = None
    side: str | None = None
    odds: int | None = None
    model_prob: float | None = None


class BetLegRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    leg_id: int
    player_name: str | None
    stat_type: str | None
    line_value: float | None
    side: str | None
    odds: int | None
    leg_status: BetStatus
    model_prob: float | None


class BetCreate(BaseModel):
    sportsbook: str
    bet_type: BetType
    stake: float
    potential_payout: float
    placed_at: datetime | None = None
    is_paper: bool = False
    legs: list[BetLegCreate] = []


class BetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bet_id: int
    sportsbook: str
    placed_at: datetime
    bet_type: BetType
    stake: float
    potential_payout: float
    status: BetStatus
    settled_at: datetime | None
    net_result: float | None
    is_paper: bool
    legs: list[BetLegRead]


class BetSettle(BaseModel):
    status: BetStatus
    net_result: float


def _bet_query(db: Session):
    return db.query(Bet).options(selectinload(Bet.legs))


@router.post("", response_model=BetRead)
def create_bet(body: BetCreate, db: Session = Depends(get_db)) -> Bet:
    bet = Bet(
        sportsbook=body.sportsbook,
        bet_type=body.bet_type,
        stake=body.stake,
        potential_payout=body.potential_payout,
        placed_at=body.placed_at or datetime.now(timezone.utc),
        status=BetStatus.pending,
        is_paper=body.is_paper,
        legs=[BetLeg(**leg.model_dump()) for leg in body.legs],
    )
    db.add(bet)
    db.commit()
    db.refresh(bet)
    return bet


@router.get("", response_model=list[BetRead])
def list_bets(status: BetStatus | None = None, db: Session = Depends(get_db)) -> list[Bet]:
    query = _bet_query(db)
    if status is not None:
        query = query.filter(Bet.status == status)
    return query.order_by(Bet.placed_at.desc()).all()


class MonthlyNetResult(BaseModel):
    month: date
    bet_net_profit: float
    bets_settled: int
    bank_net_cash_outflow: float


class NetResultTrendResponse(BaseModel):
    by_month: list[MonthlyNetResult]


@router.get("/trend", response_model=NetResultTrendResponse)
def bet_trend(start: date, end: date, db: Session = Depends(get_db)) -> NetResultTrendResponse:
    """
    Two independent views of betting activity, month over month:
    - bet_net_profit: sum of settled bets' net_result (positive = you came out ahead
      on the bets themselves), grouped by the month each bet was settled.
    - bank_net_cash_outflow: sum of is_betting transaction amounts from the bank feed
      (positive = net money sent to sportsbooks that month, negative = net money
      received back), grouped by transaction date.
    These can diverge — e.g. a bet settled this month funded from an existing
    sportsbook balance won't show up on the bank side until you withdraw.

    Paper bets (is_paper=True) are excluded from bet_net_profit/bets_settled
    since they carry no real money — the bank-transaction side is unaffected
    since paper bets never produce real transactions anyway.
    """
    settled_bets = (
        db.query(Bet)
        .filter(
            Bet.settled_at.isnot(None),
            Bet.settled_at >= start,
            Bet.settled_at < end,
            Bet.is_paper.is_(False),
        )
        .all()
    )
    bet_monthly: dict[date, dict[str, float]] = defaultdict(lambda: {"profit": 0.0, "count": 0})
    for bet in settled_bets:
        month_key = bet.settled_at.date().replace(day=1)
        bet_monthly[month_key]["profit"] += float(bet.net_result or 0)
        bet_monthly[month_key]["count"] += 1

    betting_txns = (
        db.query(Transaction)
        .filter(Transaction.is_betting.is_(True), Transaction.date >= start, Transaction.date < end)
        .all()
    )
    bank_monthly: dict[date, float] = defaultdict(float)
    for txn in betting_txns:
        month_key = txn.date.replace(day=1)
        bank_monthly[month_key] += float(txn.amount)

    all_months = sorted(set(bet_monthly) | set(bank_monthly))
    by_month = [
        MonthlyNetResult(
            month=month,
            bet_net_profit=round(bet_monthly[month]["profit"], 2),
            bets_settled=int(bet_monthly[month]["count"]),
            bank_net_cash_outflow=round(bank_monthly[month], 2),
        )
        for month in all_months
    ]
    return NetResultTrendResponse(by_month=by_month)


class AutoSettleResponse(BaseModel):
    settled_bet_ids: list[int]


@router.post("/auto-settle", response_model=AutoSettleResponse)
def auto_settle(db: Session = Depends(get_db)) -> AutoSettleResponse:
    """Cross-references pending bets against playstat's finalized box scores
    and settles whatever can be resolved. Safe to call repeatedly (e.g. on a
    daily schedule) — unresolvable bets are simply left pending.
    """
    return AutoSettleResponse(settled_bet_ids=auto_settle_pending_bets(db))


class GroupStats(BaseModel):
    key: str
    settled: int
    wins: int
    losses: int
    pushes: int
    total_staked: float
    net_profit: float
    roi: float | None


class StatTypeStats(BaseModel):
    key: str
    legs: int
    won: int
    lost: int
    pushed: int
    hit_rate: float | None


class CalibrationBucket(BaseModel):
    lo: float
    hi: float
    legs: int
    predicted: float
    actual: float


class Calibration(BaseModel):
    legs: int
    overall_predicted: float | None
    overall_actual: float | None
    buckets: list[CalibrationBucket]


class OverallStats(BaseModel):
    settled: int
    wins: int
    losses: int
    pushes: int
    total_staked: float
    net_profit: float
    roi: float | None


class BetAnalyticsResponse(BaseModel):
    scope: Literal["real", "paper"]
    overall: OverallStats
    by_sportsbook: list[GroupStats]
    by_bet_type: list[GroupStats]
    by_stat_type: list[StatTypeStats]
    calibration: Calibration


@router.get("/analytics", response_model=BetAnalyticsResponse)
def bet_analytics(
    scope: Literal["real", "paper"] = "real", db: Session = Depends(get_db)
) -> BetAnalyticsResponse:
    """Aggregated win/loss, ROI, and model-calibration stats over settled bets.

    scope=real (default) covers is_paper=False bets; scope=paper covers
    is_paper=True bets. Only settled bets (settled_at is not null) are
    included.
    """
    bets = (
        _bet_query(db)
        .filter(Bet.settled_at.isnot(None), Bet.is_paper.is_(scope == "paper"))
        .all()
    )
    return BetAnalyticsResponse(**compute_analytics(bets, scope))


class AutoLogResponse(BaseModel):
    logged_bet_ids: list[int]
    skipped_existing: int


@router.post("/auto-log-recommendations", response_model=AutoLogResponse)
def auto_log_recommendations(db: Session = Depends(get_db)) -> AutoLogResponse:
    """Logs playstat's current parlay recommendations as paper bets,
    idempotently (dedup on external_ref). Runs on a schedule, so playstat
    being unreachable returns zeros instead of a 500.
    """
    try:
        recs = get_parlay_recommendations(limit=10)
        edges = get_edges()
    except httpx.HTTPError:
        return AutoLogResponse(logged_bet_ids=[], skipped_existing=0)

    candidate_bets = recommendations_to_bets(recs, edges)

    logged_bet_ids: list[int] = []
    skipped_existing = 0
    for bet in candidate_bets:
        exists = db.query(Bet).filter(Bet.external_ref == bet.external_ref).one_or_none()
        if exists is not None:
            skipped_existing += 1
            continue
        db.add(bet)
        db.flush()
        logged_bet_ids.append(bet.bet_id)

    db.commit()
    return AutoLogResponse(logged_bet_ids=logged_bet_ids, skipped_existing=skipped_existing)


class BankrollPoint(BaseModel):
    date: str
    net: float
    cumulative: float


class BankrollResponse(BaseModel):
    scope: Literal["real", "paper"]
    points: list[BankrollPoint]
    max_drawdown: float
    longest_losing_streak: int


@router.get("/bankroll", response_model=BankrollResponse)
def bet_bankroll(
    scope: Literal["real", "paper"] = "real", db: Session = Depends(get_db)
) -> BankrollResponse:
    """Cumulative bankroll trend, max drawdown, and longest losing streak
    over settled bets in the given scope, ordered by settled_at.
    """
    bets = (
        _bet_query(db)
        .filter(Bet.settled_at.isnot(None), Bet.is_paper.is_(scope == "paper"))
        .order_by(Bet.settled_at)
        .all()
    )
    return BankrollResponse(**compute_bankroll(bets, scope))


@router.get("/{bet_id}", response_model=BetRead)
def get_bet(bet_id: int, db: Session = Depends(get_db)) -> Bet:
    bet = _bet_query(db).filter(Bet.bet_id == bet_id).one_or_none()
    if bet is None:
        raise HTTPException(status_code=404, detail=f"No bet with id {bet_id}")
    return bet


@router.patch("/{bet_id}/settle", response_model=BetRead)
def settle_bet(bet_id: int, body: BetSettle, db: Session = Depends(get_db)) -> Bet:
    bet = _bet_query(db).filter(Bet.bet_id == bet_id).one_or_none()
    if bet is None:
        raise HTTPException(status_code=404, detail=f"No bet with id {bet_id}")

    bet.status = body.status
    bet.net_result = body.net_result
    bet.settled_at = datetime.now(timezone.utc)
    for leg in bet.legs:
        leg.leg_status = body.status

    db.commit()
    db.refresh(bet)
    return bet
