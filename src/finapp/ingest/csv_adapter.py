"""CSV import: works with any bank export via auto-detected or explicit column mapping."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from finapp.models import AccountInfo, BalanceInfo, RawTransaction
from finapp.money import parse_amount, to_minor

DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%b %d, %Y",
    "%Y%m%d",
]

# Header names seen in common bank exports, matched case-insensitively.
DATE_HEADERS = ["date", "transaction date", "trans. date", "posting date", "posted date", "post date"]
DESC_HEADERS = ["description", "payee", "merchant", "name", "details", "memo", "transaction description"]
AMOUNT_HEADERS = ["amount", "transaction amount", "amount (usd)", "amount (cad)"]
DEBIT_HEADERS = ["debit", "withdrawal", "withdrawals", "debit amount", "money out"]
CREDIT_HEADERS = ["credit", "deposit", "deposits", "credit amount", "money in"]
BALANCE_HEADERS = ["balance", "running balance", "running bal.", "balance (usd)", "balance (cad)"]


@dataclass
class CsvMapping:
    """How to read one bank's export. Leave fields None to auto-detect from headers."""

    date_col: str | None = None
    description_col: str | None = None
    amount_col: str | None = None
    debit_col: str | None = None
    credit_col: str | None = None
    balance_col: str | None = None
    date_format: str | None = None
    #: flip signs, for exports that show purchases as positive (common for credit cards)
    negate: bool = False
    delimiter: str = ","
    skip_rows: int = 0
    extra_desc_cols: list[str] = field(default_factory=list)


def _find(headers: list[str], candidates: list[str]) -> str | None:
    lower = {h.strip().lower(): h for h in headers}
    for c in candidates:
        if c in lower:
            return lower[c]
    return None


def _pick_date_format(values: list[str], explicit: str | None) -> str:
    """Choose the first format that parses every value (resolves d/m vs m/d)."""
    formats = [explicit] if explicit else DATE_FORMATS
    for fmt in formats:
        try:
            for v in values:
                datetime.strptime(v.strip(), fmt)
            return fmt
        except ValueError:
            continue
    sample = values[0] if values else "<none>"
    raise ValueError(f"no date format fits all rows (e.g. {sample!r}); set date_format")


class CsvAdapter:
    source = "csv"

    def __init__(self, path: str | Path | None, account: AccountInfo, mapping: CsvMapping | None = None,
                 text: str | None = None):
        if path is None and text is None:
            raise ValueError("give a path or text")
        self.account = account
        self.mapping = mapping or CsvMapping()
        raw = text if text is not None else Path(path).read_text(encoding="utf-8-sig")
        self._rows, self._cols = self._parse(raw)

    # -- parsing --------------------------------------------------------------
    def _parse(self, raw: str):
        m = self.mapping
        lines = raw.splitlines()[m.skip_rows:]
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=m.delimiter)
        headers = [h for h in (reader.fieldnames or []) if h is not None]
        rows = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]

        cols = {
            "date": m.date_col or _find(headers, DATE_HEADERS),
            "desc": m.description_col or _find(headers, DESC_HEADERS),
            "amount": m.amount_col or _find(headers, AMOUNT_HEADERS),
            "debit": m.debit_col or _find(headers, DEBIT_HEADERS),
            "credit": m.credit_col or _find(headers, CREDIT_HEADERS),
            "balance": m.balance_col or _find(headers, BALANCE_HEADERS),
        }
        if not cols["date"] or not cols["desc"]:
            raise ValueError(f"could not find date/description columns in {headers}; set CsvMapping")
        if not cols["amount"] and not (cols["debit"] or cols["credit"]):
            raise ValueError(f"could not find amount or debit/credit columns in {headers}; set CsvMapping")
        cols["date_format"] = _pick_date_format([r[cols["date"]] for r in rows], m.date_format)
        return rows, cols

    def _date(self, row) -> date:
        return datetime.strptime(row[self._cols["date"]].strip(), self._cols["date_format"]).date()

    def _amount_minor(self, row) -> int:
        c, cur = self._cols, self.account.currency
        if c["amount"] and (row.get(c["amount"]) or "").strip():
            value = parse_amount(row[c["amount"]])
        else:
            debit = (row.get(c["debit"]) or "").strip() if c["debit"] else ""
            credit = (row.get(c["credit"]) or "").strip() if c["credit"] else ""
            value = parse_amount(credit) if credit else 0
            if debit:
                value = value - abs(parse_amount(debit))
        minor = to_minor(parse_amount(value), cur)
        return -minor if self.mapping.negate else minor

    def _descriptor(self, row) -> str:
        parts = [row[self._cols["desc"]]] + [row.get(c, "") for c in self.mapping.extra_desc_cols]
        return " ".join(p.strip() for p in parts if p and p.strip())

    # -- Adapter API ----------------------------------------------------------
    def list_accounts(self) -> list[AccountInfo]:
        return [self.account]

    def fetch_transactions(self, since: date | None = None) -> list[RawTransaction]:
        out = []
        for row in self._rows:
            d = self._date(row)
            if since and d < since:
                continue
            out.append(
                RawTransaction(
                    account_key=self.account.external_key,
                    posted_at=d,
                    amount_minor=self._amount_minor(row),
                    currency=self.account.currency,
                    descriptor=self._descriptor(row),
                )
            )
        return out

    def fetch_balances(self) -> list[BalanceInfo]:
        """End-of-day balances from a running-balance column, if the export has one."""
        col = self._cols["balance"]
        if not col or not self._rows:
            return []
        dated = [(self._date(r), r) for r in self._rows if (r.get(col) or "").strip()]
        if not dated:
            return []
        # Exports run newest-first or oldest-first; the end-of-day balance is the
        # chronologically last row of each date.
        descending = dated[0][0] > dated[-1][0]
        by_day: dict[date, str] = {}
        for d, r in dated:
            if descending:
                by_day.setdefault(d, r[col])
            else:
                by_day[d] = r[col]
        cur = self.account.currency
        return [
            BalanceInfo(account_key=self.account.external_key, date=d,
                        balance_minor=to_minor(parse_amount(v), cur), currency=cur)
            for d, v in sorted(by_day.items())
        ]
