from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import Alert, BudgetPeriod, Category, Transaction

router = APIRouter(tags=["budgeting"])

DEFAULT_ALERT_THRESHOLDS = (80.0, 100.0)


def _month_bounds(month: date) -> tuple[date, date]:
    month_start = month.replace(day=1)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return month_start, next_month


# ---- Categories ----


class CategoryCreate(BaseModel):
    name: str
    monthly_limit: float
    is_betting_category: bool = False


class CategoryUpdate(BaseModel):
    name: str | None = None
    monthly_limit: float | None = None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: int
    name: str
    monthly_limit: float
    is_betting_category: bool


@router.post("/categories", response_model=CategoryRead)
def create_category(body: CategoryCreate, db: Session = Depends(get_db)) -> Category:
    category = Category(**body.model_dump())
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists") from exc
    db.refresh(category)
    return category


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)) -> list[Category]:
    return db.query(Category).order_by(Category.name).all()


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: int, body: CategoryUpdate, db: Session = Depends(get_db)
) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail=f"No category with id {category_id}")

    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(category, key, value)

    db.commit()
    db.refresh(category)
    return category


# ---- Budget periods ----


class BudgetPeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_id: int
    category_id: int
    month: date
    spent: float
    limit: float
    remaining: float


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_id: int
    category_id: int
    month: date
    threshold_pct: float
    triggered_at: datetime | None
    message: str | None


class RecomputeResponse(BaseModel):
    budget_periods: list[BudgetPeriodRead]
    alerts_fired: list[AlertRead]


def _spent_for_category(db: Session, category: Category, month_start: date, month_end: date) -> float:
    query = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.date >= month_start, Transaction.date < month_end
    )
    if category.is_betting_category:
        query = query.filter(Transaction.is_betting.is_(True))
    else:
        query = query.filter(Transaction.custom_category == category.name)
    return float(query.scalar())


def recompute_budget_periods_for_month(db: Session, month: date) -> RecomputeResponse:
    """Core recompute logic, reusable outside this router (e.g. plaid.py calls
    this after a transaction sync or re-categorization so spent/remaining
    reflects reality without the caller having to know about this endpoint).
    """
    month_start, month_end = _month_bounds(month)
    categories = db.query(Category).all()

    periods: list[BudgetPeriod] = []
    alerts_fired: list[Alert] = []

    for category in categories:
        spent = _spent_for_category(db, category, month_start, month_end)

        period = (
            db.query(BudgetPeriod)
            .filter(BudgetPeriod.category_id == category.category_id, BudgetPeriod.month == month_start)
            .one_or_none()
        )
        limit = float(category.monthly_limit)
        remaining = limit - spent

        if period is None:
            period = BudgetPeriod(
                category_id=category.category_id,
                month=month_start,
                spent=spent,
                limit=limit,
                remaining=remaining,
            )
            db.add(period)
        else:
            period.spent = spent
            period.limit = limit
            period.remaining = remaining

        periods.append(period)

        if limit > 0:
            pct = spent / limit * 100
            for threshold in DEFAULT_ALERT_THRESHOLDS:
                if pct < threshold:
                    continue
                existing_alert = (
                    db.query(Alert)
                    .filter(
                        Alert.category_id == category.category_id,
                        Alert.month == month_start,
                        Alert.threshold_pct == threshold,
                    )
                    .one_or_none()
                )
                if existing_alert is not None:
                    continue
                alert = Alert(
                    category_id=category.category_id,
                    month=month_start,
                    threshold_pct=threshold,
                    triggered_at=datetime.now(timezone.utc),
                    message=(
                        f"{category.name} has reached {pct:.0f}% of its "
                        f"${limit:.2f} monthly limit (${spent:.2f} spent)."
                    ),
                )
                db.add(alert)
                alerts_fired.append(alert)

    db.commit()
    for period in periods:
        db.refresh(period)
    for alert in alerts_fired:
        db.refresh(alert)

    return RecomputeResponse(budget_periods=periods, alerts_fired=alerts_fired)


@router.post("/budget-periods/recompute", response_model=RecomputeResponse)
def recompute_budget_periods(month: date, db: Session = Depends(get_db)) -> RecomputeResponse:
    return recompute_budget_periods_for_month(db, month)


@router.get("/budget-periods", response_model=list[BudgetPeriodRead])
def list_budget_periods(month: date, db: Session = Depends(get_db)) -> list[BudgetPeriod]:
    month_start, _ = _month_bounds(month)
    return db.query(BudgetPeriod).filter(BudgetPeriod.month == month_start).all()


# ---- Alerts ----


@router.get("/alerts", response_model=list[AlertRead])
def list_alerts(month: date | None = None, db: Session = Depends(get_db)) -> list[Alert]:
    query = db.query(Alert)
    if month is not None:
        month_start, _ = _month_bounds(month)
        query = query.filter(Alert.month == month_start)
    return query.order_by(Alert.triggered_at.desc()).all()
