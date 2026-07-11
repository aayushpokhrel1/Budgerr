from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import (
    Account,
    CapPeriod,
    CardRewardProgress,
    CardRewardRate,
    Category,
    CreditCard,
    Transaction,
)

router = APIRouter(prefix="/rewards", tags=["rewards"])

# No specific reward rate applies: assume a generic 1% baseline, same convention
# card issuers use for "everything else" spend.
BASELINE_MULTIPLIER = 1.0


def _quarter_bounds(d: date) -> tuple[date, date]:
    quarter_start_month = (d.month - 1) // 3 * 3 + 1
    start = d.replace(month=quarter_start_month, day=1)
    if quarter_start_month + 3 > 12:
        end = start.replace(year=start.year + 1, month=quarter_start_month + 3 - 12)
    else:
        end = start.replace(month=quarter_start_month + 3)
    return start, end


def _annual_bounds(d: date) -> tuple[date, date]:
    return date(d.year, 1, 1), date(d.year + 1, 1, 1)


def _period_bounds(cap_period: CapPeriod, d: date) -> tuple[date, date]:
    return _quarter_bounds(d) if cap_period == CapPeriod.quarterly else _annual_bounds(d)


def _rate_active_on(rate: CardRewardRate, d: date) -> bool:
    if rate.effective_start > d:
        return False
    if rate.effective_end is not None and rate.effective_end < d:
        return False
    return True


# ---- Credit cards ----


class CreditCardCreate(BaseModel):
    name: str
    issuer: str
    nickname: str | None = None
    linked_account_id: int | None = None


class CreditCardUpdate(BaseModel):
    name: str | None = None
    nickname: str | None = None
    linked_account_id: int | None = None


class CreditCardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    card_id: int
    name: str
    issuer: str
    nickname: str | None
    linked_account_id: int | None


@router.post("/cards", response_model=CreditCardRead)
def create_credit_card(body: CreditCardCreate, db: Session = Depends(get_db)) -> CreditCard:
    card = CreditCard(**body.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.get("/cards", response_model=list[CreditCardRead])
def list_credit_cards(db: Session = Depends(get_db)) -> list[CreditCard]:
    return db.query(CreditCard).order_by(CreditCard.name).all()


@router.patch("/cards/{card_id}", response_model=CreditCardRead)
def update_credit_card(
    card_id: int, body: CreditCardUpdate, db: Session = Depends(get_db)
) -> CreditCard:
    card = db.get(CreditCard, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"No credit card with id {card_id}")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(card, key, value)
    db.commit()
    db.refresh(card)
    return card


# ---- Reward rates ----


class RewardRateCreate(BaseModel):
    category_id: int
    multiplier: float
    cap_amount: float | None = None
    cap_period: CapPeriod | None = None
    effective_start: date
    effective_end: date | None = None


class RewardRateUpdate(BaseModel):
    multiplier: float | None = None
    cap_amount: float | None = None
    cap_period: CapPeriod | None = None
    effective_end: date | None = None


class RewardRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rate_id: int
    card_id: int
    category_id: int
    multiplier: float
    cap_amount: float | None
    cap_period: CapPeriod | None
    effective_start: date
    effective_end: date | None


@router.post("/cards/{card_id}/reward-rates", response_model=RewardRateRead)
def create_reward_rate(
    card_id: int, body: RewardRateCreate, db: Session = Depends(get_db)
) -> CardRewardRate:
    if db.get(CreditCard, card_id) is None:
        raise HTTPException(status_code=404, detail=f"No credit card with id {card_id}")
    if db.get(Category, body.category_id) is None:
        raise HTTPException(status_code=404, detail=f"No category with id {body.category_id}")

    rate = CardRewardRate(card_id=card_id, **body.model_dump())
    db.add(rate)
    db.commit()
    db.refresh(rate)
    return rate


@router.get("/cards/{card_id}/reward-rates", response_model=list[RewardRateRead])
def list_reward_rates(card_id: int, db: Session = Depends(get_db)) -> list[CardRewardRate]:
    return db.query(CardRewardRate).filter(CardRewardRate.card_id == card_id).all()


@router.patch("/reward-rates/{rate_id}", response_model=RewardRateRead)
def update_reward_rate(
    rate_id: int, body: RewardRateUpdate, db: Session = Depends(get_db)
) -> CardRewardRate:
    rate = db.get(CardRewardRate, rate_id)
    if rate is None:
        raise HTTPException(status_code=404, detail=f"No reward rate with id {rate_id}")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(rate, key, value)
    db.commit()
    db.refresh(rate)
    return rate


# ---- Reward progress (cap tracking) ----


class RewardProgressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    progress_id: int
    card_id: int
    category_id: int
    period_start: date
    period_end: date
    amount_spent_at_bonus_rate: float


@router.post("/progress/recompute", response_model=list[RewardProgressRead])
def recompute_reward_progress(
    as_of: date, db: Session = Depends(get_db)
) -> list[CardRewardProgress]:
    capped_rates = (
        db.query(CardRewardRate)
        .filter(CardRewardRate.cap_amount.isnot(None), CardRewardRate.cap_period.isnot(None))
        .all()
    )

    updated: list[CardRewardProgress] = []

    for rate in capped_rates:
        if not _rate_active_on(rate, as_of):
            continue

        card = db.get(CreditCard, rate.card_id)
        if card is None or card.linked_account_id is None:
            continue

        category = db.get(Category, rate.category_id)
        period_start, period_end = _period_bounds(rate.cap_period, as_of)

        spent = (
            db.query(func.coalesce(func.sum(Transaction.amount), 0))
            .filter(
                Transaction.account_id == card.linked_account_id,
                Transaction.custom_category == category.name,
                Transaction.date >= period_start,
                Transaction.date < period_end,
            )
            .scalar()
        )

        progress = (
            db.query(CardRewardProgress)
            .filter(
                CardRewardProgress.card_id == card.card_id,
                CardRewardProgress.category_id == category.category_id,
                CardRewardProgress.period_start == period_start,
            )
            .one_or_none()
        )
        if progress is None:
            progress = CardRewardProgress(
                card_id=card.card_id,
                category_id=category.category_id,
                period_start=period_start,
                period_end=period_end,
                amount_spent_at_bonus_rate=float(spent),
            )
            db.add(progress)
        else:
            progress.amount_spent_at_bonus_rate = float(spent)

        updated.append(progress)

    db.commit()
    for progress in updated:
        db.refresh(progress)
    return updated


@router.get("/progress", response_model=list[RewardProgressRead])
def list_reward_progress(
    card_id: int | None = None, category_id: int | None = None, db: Session = Depends(get_db)
) -> list[CardRewardProgress]:
    query = db.query(CardRewardProgress)
    if card_id is not None:
        query = query.filter(CardRewardProgress.card_id == card_id)
    if category_id is not None:
        query = query.filter(CardRewardProgress.category_id == category_id)
    return query.order_by(CardRewardProgress.period_start.desc()).all()


# ---- Proactive: best card right now ----


class BestCardOption(BaseModel):
    card_id: int
    card_name: str
    multiplier: float
    capped_out: bool
    remaining_cap_room: float | None


class BestCardResponse(BaseModel):
    best: BestCardOption | None
    options: list[BestCardOption]


@router.get("/best-card", response_model=BestCardResponse)
def best_card(category_id: int, as_of: date | None = None, db: Session = Depends(get_db)) -> BestCardResponse:
    as_of = as_of or date.today()

    if db.get(Category, category_id) is None:
        raise HTTPException(status_code=404, detail=f"No category with id {category_id}")

    rates = db.query(CardRewardRate).filter(CardRewardRate.category_id == category_id).all()

    options: list[BestCardOption] = []
    for rate in rates:
        if not _rate_active_on(rate, as_of):
            continue

        card = db.get(CreditCard, rate.card_id)
        capped_out = False
        remaining_cap_room = None

        if rate.cap_amount is not None and rate.cap_period is not None:
            period_start, _ = _period_bounds(rate.cap_period, as_of)
            progress = (
                db.query(CardRewardProgress)
                .filter(
                    CardRewardProgress.card_id == card.card_id,
                    CardRewardProgress.category_id == category_id,
                    CardRewardProgress.period_start == period_start,
                )
                .one_or_none()
            )
            spent_so_far = float(progress.amount_spent_at_bonus_rate) if progress is not None else 0.0
            remaining_cap_room = float(rate.cap_amount) - spent_so_far
            capped_out = remaining_cap_room <= 0

        options.append(
            BestCardOption(
                card_id=card.card_id,
                card_name=card.name,
                multiplier=float(rate.multiplier),
                capped_out=capped_out,
                remaining_cap_room=remaining_cap_room,
            )
        )

    active_options = [o for o in options if not o.capped_out]
    active_options.sort(key=lambda o: o.multiplier, reverse=True)

    return BestCardResponse(best=active_options[0] if active_options else None, options=options)


# ---- Retrospective: rewards left on the table ----


class MonthlyGap(BaseModel):
    month: date
    gap_dollars: float
    transaction_count: int


class LeftOnTableResponse(BaseModel):
    total_gap_dollars: float
    by_month: list[MonthlyGap]


@router.get("/left-on-table", response_model=LeftOnTableResponse)
def rewards_left_on_table(
    start: date, end: date, db: Session = Depends(get_db)
) -> LeftOnTableResponse:
    linked_cards = db.query(CreditCard).filter(CreditCard.linked_account_id.isnot(None)).all()
    account_to_card = {c.linked_account_id: c for c in linked_cards}
    if not account_to_card:
        return LeftOnTableResponse(total_gap_dollars=0.0, by_month=[])

    categories_by_name = {c.name: c for c in db.query(Category).all()}

    all_rates = db.query(CardRewardRate).all()
    rates_by_category: dict[int, list[CardRewardRate]] = defaultdict(list)
    for rate in all_rates:
        rates_by_category[rate.category_id].append(rate)

    transactions = (
        db.query(Transaction)
        .filter(
            Transaction.account_id.in_(account_to_card.keys()),
            Transaction.custom_category.isnot(None),
            Transaction.date >= start,
            Transaction.date < end,
        )
        .all()
    )

    monthly: dict[date, dict[str, float]] = defaultdict(lambda: {"gap": 0.0, "count": 0})

    for txn in transactions:
        category = categories_by_name.get(txn.custom_category)
        if category is None:
            continue

        card = account_to_card[txn.account_id]
        candidate_rates = [r for r in rates_by_category[category.category_id] if _rate_active_on(r, txn.date)]

        actual_rate = next((r for r in candidate_rates if r.card_id == card.card_id), None)
        actual_multiplier = float(actual_rate.multiplier) if actual_rate is not None else BASELINE_MULTIPLIER

        optimal_multiplier = max(
            (float(r.multiplier) for r in candidate_rates), default=actual_multiplier
        )
        optimal_multiplier = max(optimal_multiplier, actual_multiplier)

        gap = (optimal_multiplier - actual_multiplier) / 100 * float(txn.amount)
        if gap <= 0:
            continue

        month_key = txn.date.replace(day=1)
        monthly[month_key]["gap"] += gap
        monthly[month_key]["count"] += 1

    by_month = [
        MonthlyGap(month=month, gap_dollars=round(values["gap"], 2), transaction_count=int(values["count"]))
        for month, values in sorted(monthly.items())
    ]
    total_gap = round(sum(m.gap_dollars for m in by_month), 2)

    return LeftOnTableResponse(total_gap_dollars=total_gap, by_month=by_month)
