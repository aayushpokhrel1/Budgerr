from datetime import date as date_
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.accounts import Account


class Transaction(Base):
    __tablename__ = "transactions"

    txn_id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.account_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plaid_transaction_id: Mapped[str | None] = mapped_column(String, unique=True)
    date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    merchant_name: Mapped[str | None] = mapped_column(String)
    plaid_category: Mapped[str | None] = mapped_column(String)
    custom_category: Mapped[str | None] = mapped_column(String)
    is_betting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    account: Mapped["Account"] = relationship(back_populates="transactions")
