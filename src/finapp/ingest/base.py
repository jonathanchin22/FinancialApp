"""The ingestion contract every data source implements.

Live providers (bank aggregators) and file imports (CSV, OFX) all plug in here, so
no single provider is a point of failure: swap or add sources without touching the
rest of the app.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from typing import Iterable, Protocol, runtime_checkable

from finapp.models import AccountInfo, BalanceInfo, RawTransaction


@runtime_checkable
class Adapter(Protocol):
    #: short source name stored on every row, e.g. 'csv', 'ofx'
    source: str

    def list_accounts(self) -> list[AccountInfo]: ...

    def fetch_transactions(self, since: date | None = None) -> list[RawTransaction]: ...

    def fetch_balances(self) -> list[BalanceInfo]: ...


def source_hashes(txns: Iterable[RawTransaction]) -> list[tuple[RawTransaction, str]]:
    """Attach a content hash to each transaction for cross-source deduplication.

    The hash deliberately ignores the descriptor and the source's own id: the same
    purchase arrives as "SQ *BLUE BOTTLE 0423" from one source and "Blue Bottle
    Coffee" from another. Identity is (account, date, amount, currency) plus an
    occurrence index, so two genuine $4.50 coffees on the same day stay distinct,
    and re-importing an overlapping export matches the same rows again.

    Pending transactions are hashed separately; reconciling pending to posted is
    Phase 3 work.
    """
    seen: dict[tuple, int] = defaultdict(int)
    out = []
    for t in sorted(txns, key=lambda t: (t.account_key, t.posted_at, t.amount_minor)):
        key = (t.account_key, t.posted_at.isoformat(), t.amount_minor, t.currency.upper(), t.pending)
        n = seen[key]
        seen[key] += 1
        payload = "|".join(str(k) for k in key) + f"|{n}"
        out.append((t, hashlib.sha256(payload.encode()).hexdigest()))
    return out
