"""Domain types shared by ingestion, the repository and the API."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

# Tax residency drives which accounts count as "foreign" (FBAR / Form 8938).
TAX_RESIDENCY = "US"


class FlowType(str, Enum):
    SPEND = "spend"
    INCOME = "income"
    TRANSFER = "transfer"
    INVESTMENT = "investment"
    REFUND = "refund"


class AccountType(str, Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment"
    RETIREMENT = "retirement"
    LOAN = "loan"
    CASH = "cash"
    OTHER = "other"


class AccountInfo(BaseModel):
    """An account as described by an ingestion source."""

    external_key: str
    name: str
    type: AccountType
    currency: str = Field(min_length=3, max_length=3)
    institution: str
    country: str = Field(min_length=2, max_length=2)

    @property
    def is_foreign(self) -> bool:
        return self.country.upper() != TAX_RESIDENCY


class RawTransaction(BaseModel):
    """A transaction as produced by an adapter, before storage."""

    account_key: str
    posted_at: date
    amount_minor: int
    currency: str
    descriptor: str
    pending: bool = False
    external_id: str | None = None


class BalanceInfo(BaseModel):
    account_key: str
    date: date
    balance_minor: int
    currency: str
