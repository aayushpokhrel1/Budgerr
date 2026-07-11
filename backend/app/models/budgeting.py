from datetime import date as date_
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    pass


class Category(Base):
    __tablename__ = "categories"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    monthly_limit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    is_betting_category: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    budget_periods: Mapped[list["BudgetPeriod"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class BudgetPeriod(Base):
    __tablename__ = "budget_periods"
    __table_args__ = (UniqueConstraint("category_id", "month", name="uq_budget_period_category_month"),)

    period_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.category_id", ondelete="CASCADE"), nullable=False, index=True
    )
    month: Mapped[date_] = mapped_column(Date, nullable=False)
    spent: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    limit: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    remaining: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    category: Mapped["Category"] = relationship(back_populates="budget_periods")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("category_id", "month", "threshold_pct", name="uq_alert_category_month_threshold"),
    )

    alert_id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.category_id", ondelete="CASCADE"), nullable=False, index=True
    )
    month: Mapped[date_] = mapped_column(Date, nullable=False)
    threshold_pct: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(String)

    category: Mapped["Category"] = relationship(back_populates="alerts")
