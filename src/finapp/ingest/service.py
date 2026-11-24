"""Run any adapter into the database: accounts, deduplicated transactions, balances, sync state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from finapp.ingest.base import Adapter, source_hashes
from finapp.repository import Repository


@dataclass
class AccountResult:
    account_id: int
    name: str
    inserted: int = 0
    duplicates: int = 0
    snapshots: int = 0


@dataclass
class ImportResult:
    source: str
    accounts: list[AccountResult] = field(default_factory=list)
    error: str | None = None

    @property
    def inserted(self) -> int:
        return sum(a.inserted for a in self.accounts)

    @property
    def duplicates(self) -> int:
        return sum(a.duplicates for a in self.accounts)


def run_import(repo: Repository, adapter: Adapter, since: date | None = None) -> ImportResult:
    result = ImportResult(source=adapter.source)
    by_key: dict[str, AccountResult] = {}
    try:
        for info in adapter.list_accounts():
            acct_id = repo.ensure_account(info, adapter.source)
            by_key[info.external_key] = AccountResult(account_id=acct_id, name=info.name)

        for txn, h in source_hashes(adapter.fetch_transactions(since)):
            acct = by_key[txn.account_key]
            if repo.insert_transaction(acct.account_id, txn, adapter.source, h) is None:
                acct.duplicates += 1
            else:
                acct.inserted += 1

        for bal in adapter.fetch_balances():
            acct = by_key[bal.account_key]
            repo.record_snapshot(acct.account_id, bal.date, bal.balance_minor, bal.currency, adapter.source)
            acct.snapshots += 1

        for acct in by_key.values():
            repo.record_sync(acct.account_id, adapter.source)
    except Exception as e:  # surface failures in sync state, never fail silently
        result.error = f"{type(e).__name__}: {e}"
        for acct in by_key.values():
            repo.record_sync(acct.account_id, adapter.source, error=result.error)
        raise
    finally:
        result.accounts = list(by_key.values())
    return result
