import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    pass


class BetType(str, enum.Enum):
    single = "single"
    parlay = "parlay"


class BetStatus(str, enum.Enum):
    pending = "pending"
    won = "won"
    lost = "lost"
    push = "push"
    cashed_out = "cashed_out"


class Bet(Base):
    __tablename__ = "bets"

    bet_id: Mapped[int] = mapped_column(primary_key=True)
    sportsbook: Mapped[str] = mapped_column(String, nullable=False, index=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bet_type: Mapped[BetType] = mapped_column(Enum(BetType, name="bet_type"), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    potential_payout: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[BetStatus] = mapped_column(
        Enum(BetStatus, name="bet_status"), nullable=False, default=BetStatus.pending
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    net_result: Mapped[float | None] = mapped_column(Numeric(12, 2))

    legs: Mapped[list["BetLeg"]] = relationship(back_populates="bet", cascade="all, delete-orphan")


class BetLeg(Base):
    __tablename__ = "bet_legs"

    leg_id: Mapped[int] = mapped_column(primary_key=True)
    bet_id: Mapped[int] = mapped_column(
        ForeignKey("bets.bet_id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_name: Mapped[str | None] = mapped_column(String)
    stat_type: Mapped[str | None] = mapped_column(String)
    line_value: Mapped[float | None] = mapped_column(Numeric(6, 2))
    side: Mapped[str | None] = mapped_column(String)
    odds: Mapped[int | None] = mapped_column()
    leg_status: Mapped[BetStatus] = mapped_column(
        Enum(BetStatus, name="bet_status"), nullable=False, default=BetStatus.pending
    )

    bet: Mapped["Bet"] = relationship(back_populates="legs")
