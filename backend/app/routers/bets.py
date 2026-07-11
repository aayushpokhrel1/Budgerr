from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, selectinload

from app.deps import get_db
from app.models import Bet, BetLeg, BetStatus, BetType

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
