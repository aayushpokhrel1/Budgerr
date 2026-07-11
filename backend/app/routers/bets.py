from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, selectinload

from app.deps import get_db
from app.models import Bet, BetLeg, BetStatus, BetType, Transaction

router = APIRouter(prefix="/bets", tags=["bets"])


class BetLegCreate(BaseModel):
    player_name: str | None = None
    stat_type: str | None = None
    line_value: float | None = None
    side: str | None = None
    odds: int | None = None


class BetLegRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    leg_id: int
    player_name: str | None
    stat_type: str | None
    line_value: float | None
    side: str | None
    odds: int | None
    leg_status: BetStatus


class BetCreate(BaseModel):
    sportsbook: str
    bet_type: BetType
    stake: float
    potential_payout: float
    placed_at: datetime | None = None
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
    """
    settled_bets = (
        db.query(Bet)
        .filter(Bet.settled_at.isnot(None), Bet.settled_at >= start, Bet.settled_at < end)
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
