from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.accounts import Account


class PlaidItem(Base):
    __tablename__ = "plaid_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    institution_name: Mapped[str] = mapped_column(String, nullable=False)
    cursor: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="plaid_item", cascade="all, delete-orphan"
    )
