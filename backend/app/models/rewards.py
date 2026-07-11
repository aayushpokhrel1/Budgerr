import enum
from datetime import date as date_
from typing import TYPE_CHECKING

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.accounts import Account
    from app.models.budgeting import Category


class CapPeriod(str, enum.Enum):
    quarterly = "quarterly"
    annual = "annual"


class CreditCard(Base):
    __tablename__ = "credit_cards"

    card_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    issuer: Mapped[str] = mapped_column(String, nullable=False)
    nickname: Mapped[str | None] = mapped_column(String)
    linked_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="SET NULL"), unique=True
    )

    linked_account: Mapped["Account | None"] = relationship()
    reward_rates: Mapped[list["CardRewardRate"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )
    reward_progress: Mapped[list["CardRewardProgress"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class CardRewardRate(Base):
    __tablename__ = "card_reward_rates"

    rate_id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("credit_cards.card_id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.category_id", ondelete="CASCADE"), nullable=False, index=True
    )
    multiplier: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    cap_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    cap_period: Mapped[CapPeriod | None] = mapped_column(Enum(CapPeriod, name="cap_period"))
    effective_start: Mapped[date_] = mapped_column(Date, nullable=False)
    effective_end: Mapped[date_ | None] = mapped_column(Date)

    card: Mapped["CreditCard"] = relationship(back_populates="reward_rates")
    category: Mapped["Category"] = relationship()


class CardRewardProgress(Base):
    __tablename__ = "card_reward_progress"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "category_id", "period_start", name="uq_reward_progress_card_category_period"
        ),
    )

    progress_id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("credit_cards.card_id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.category_id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date_] = mapped_column(Date, nullable=False)
    period_end: Mapped[date_] = mapped_column(Date, nullable=False)
    amount_spent_at_bonus_rate: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    card: Mapped["CreditCard"] = relationship(back_populates="reward_progress")
    category: Mapped["Category"] = relationship()
