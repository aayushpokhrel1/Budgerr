from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.plaid_items import PlaidItem
    from app.models.transactions import Transaction


class Account(Base):
    __tablename__ = "accounts"

    account_id: Mapped[int] = mapped_column(primary_key=True)
    plaid_item_id: Mapped[str] = mapped_column(
        ForeignKey("plaid_items.item_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plaid_account_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    institution_name: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    mask: Mapped[str] = mapped_column(String(4), nullable=False)
    current_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    plaid_item: Mapped["PlaidItem"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
