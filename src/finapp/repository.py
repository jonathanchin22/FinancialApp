"""Repository: the only module that writes SQL. Swap for Postgres later behind this API."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

from finapp.models import AccountInfo, FlowType, RawTransaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- accounts -----------------------------------------------------------
    def ensure_account(self, info: AccountInfo, source: str) -> int:
        """Create the institution and account if new; return the account id."""
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO institution (name, country, source) VALUES (?, ?, ?)",
                (info.institution, info.country.upper(), source),
            )
            inst_id = self.conn.execute(
                "SELECT id FROM institution WHERE name = ? AND country = ?",
                (info.institution, info.country.upper()),
            ).fetchone()[0]
            self.conn.execute(
                "INSERT OR IGNORE INTO account"
                " (institution_id, external_key, name, type, currency, jurisdiction, is_foreign)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    inst_id,
                    info.external_key,
                    info.name,
                    info.type.value,
                    info.currency.upper(),
                    info.country.upper(),
                    int(info.is_foreign),
                ),
            )
            acct_id = self.account_id(info.external_key)
            self.conn.execute(
                "INSERT OR IGNORE INTO sync_state (account_id, source) VALUES (?, ?)",
                (acct_id, source),
            )
        return acct_id

    def account_id(self, external_key: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM account WHERE external_key = ?", (external_key,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown account {external_key!r}")
        return row[0]

    def get_account(self, account_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM account WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown account id {account_id}")
        return row

    def list_accounts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT a.*, i.name AS institution, s.status AS sync_status,"
            " s.last_success_at, s.last_attempt_at, s.error AS sync_error"
            " FROM account a JOIN institution i ON i.id = a.institution_id"
            " LEFT JOIN sync_state s ON s.account_id = a.id ORDER BY a.id"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- transactions -------------------------------------------------------
    def insert_transaction(
        self, account_id: int, txn: RawTransaction, source: str, source_hash: str
    ) -> int | None:
        """Insert unless the hash exists. New transactions get one default split.

        Returns the new id, or None when it was a duplicate.
        """
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO txn (account_id, posted_at, pending, amount_minor, currency,"
                " raw_descriptor, external_id, source, source_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id,
                    txn.posted_at.isoformat(),
                    int(txn.pending),
                    txn.amount_minor,
                    txn.currency.upper(),
                    txn.descriptor,
                    txn.external_id,
                    source,
                    source_hash,
                ),
            )
            if cur.rowcount == 0:
                return None
            txn_id = cur.lastrowid
            flow = FlowType.INCOME if txn.amount_minor > 0 else FlowType.SPEND
            self.conn.execute(
                "INSERT INTO split (txn_id, amount_minor, flow_type) VALUES (?, ?, ?)",
                (txn_id, txn.amount_minor, flow.value),
            )
        return txn_id

    def list_transactions(
        self,
        account_id: int | None = None,
        start: date | None = None,
        end: date | None = None,
        limit: int = 500,
    ) -> list[dict]:
        sql = (
            "SELECT t.*, a.name AS account_name FROM txn t"
            " JOIN account a ON a.id = t.account_id WHERE 1=1"
        )
        args: list = []
        if account_id is not None:
            sql += " AND t.account_id = ?"
            args.append(account_id)
        if start is not None:
            sql += " AND t.posted_at >= ?"
            args.append(start.isoformat())
        if end is not None:
            sql += " AND t.posted_at <= ?"
            args.append(end.isoformat())
        sql += " ORDER BY t.posted_at DESC, t.id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args)]

    def splits_for(self, txn_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM split WHERE txn_id = ? ORDER BY id", (txn_id,))
        return [dict(r) for r in rows]

    # -- sync state ---------------------------------------------------------
    def record_sync(self, account_id: int, source: str, error: str | None = None) -> None:
        now = _now()
        with self.conn:
            if error is None:
                self.conn.execute(
                    "UPDATE sync_state SET status='ok', last_success_at=?, last_attempt_at=?,"
                    " error=NULL, source=? WHERE account_id=?",
                    (now, now, source, account_id),
                )
            else:
                self.conn.execute(
                    "UPDATE sync_state SET status='error', last_attempt_at=?, error=?, source=?"
                    " WHERE account_id=?",
                    (now, error, source, account_id),
                )

    # -- balances -----------------------------------------------------------
    def record_snapshot(
        self, account_id: int, on: date, balance_minor: int, currency: str, source: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO balance_snapshot (account_id, date, balance_minor, currency, source)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(account_id, date) DO UPDATE SET"
                " balance_minor=excluded.balance_minor, currency=excluded.currency,"
                " source=excluded.source",
                (account_id, on.isoformat(), balance_minor, currency.upper(), source),
            )

    def latest_balance(self, account_id: int, on_or_before: date) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM balance_snapshot WHERE account_id = ? AND date <= ?"
            " ORDER BY date DESC LIMIT 1",
            (account_id, on_or_before.isoformat()),
        ).fetchone()

    def snapshots(self, account_id: int, start: date, end: date) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM balance_snapshot WHERE account_id = ? AND date BETWEEN ? AND ?"
            " ORDER BY date",
            (account_id, start.isoformat(), end.isoformat()),
        ).fetchall()

    # -- fx -----------------------------------------------------------------
    def upsert_fx(self, rows: list[tuple[date, str, str, Decimal, str]]) -> int:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO fx_rate (date, base, quote, rate, source) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(date, base, quote, source) DO UPDATE SET rate=excluded.rate",
                [(d.isoformat(), b.upper(), q.upper(), str(r), s) for d, b, q, r, s in rows],
            )
        return len(rows)

    def fx_on_or_before(
        self, on: date, base: str, quote: str, source: str | None = None
    ) -> sqlite3.Row | None:
        sql = "SELECT * FROM fx_rate WHERE base = ? AND quote = ? AND date <= ?"
        args: list = [base.upper(), quote.upper(), on.isoformat()]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY date DESC LIMIT 1"
        return self.conn.execute(sql, args).fetchone()

    def latest_fx_date(self, base: str, quote: str, source: str) -> date | None:
        row = self.conn.execute(
            "SELECT MAX(date) FROM fx_rate WHERE base = ? AND quote = ? AND source = ?",
            (base.upper(), quote.upper(), source),
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
