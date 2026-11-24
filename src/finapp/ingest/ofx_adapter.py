"""OFX / QFX import (both SGML OFX 1.x and XML OFX 2.x), standard library only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from finapp.models import AccountInfo, AccountType, BalanceInfo, RawTransaction
from finapp.money import parse_amount, to_minor

_TOKEN = re.compile(r"<(/?)([A-Za-z0-9.]+)>([^<]*)")
_ACCT_TYPES = {
    "CHECKING": AccountType.CHECKING,
    "SAVINGS": AccountType.SAVINGS,
    "MONEYMRKT": AccountType.SAVINGS,
    "CREDITLINE": AccountType.CREDIT_CARD,
}
_COUNTRY_BY_CURRENCY = {"USD": "US", "CAD": "CA"}


@dataclass
class Node:
    name: str
    fields: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)

    def find(self, name: str) -> "Node | None":
        for c in self.children:
            if c.name == name:
                return c
            hit = c.find(name)
            if hit is not None:
                return hit
        return None

    def find_all(self, name: str) -> list["Node"]:
        out = []
        for c in self.children:
            if c.name == name:
                out.append(c)
            out.extend(c.find_all(name))
        return out


def parse_ofx(text: str) -> Node:
    """Parse OFX into a tree of aggregates (children) holding leaf values (fields).

    SGML OFX leaves have no closing tag; XML OFX closes everything. Both work:
    a tag followed by text is a leaf, a tag with no text opens an aggregate.
    """
    start = text.upper().find("<OFX>")
    if start < 0:
        raise ValueError("not an OFX file: no <OFX> element")
    root = Node("ROOT")
    stack = [root]
    last_leaf: str | None = None
    for closing, name, value in _TOKEN.findall(text[start:]):
        name = name.upper()
        value = value.strip()
        if not closing:
            if value:
                stack[-1].fields[name] = value
                last_leaf = name
            else:
                node = Node(name)
                stack[-1].children.append(node)
                stack.append(node)
                last_leaf = None
        else:
            if name == last_leaf:
                last_leaf = None
                continue
            if any(n.name == name for n in stack[1:]):
                while stack[-1].name != name:
                    stack.pop()
                stack.pop()
            last_leaf = None
    return root


def _ofx_date(value: str) -> date:
    v = value.strip()[:8]
    return date(int(v[:4]), int(v[4:6]), int(v[6:8]))


class OfxAdapter:
    source = "ofx"

    def __init__(self, path: str | Path | None = None, *, text: str | None = None,
                 institution: str | None = None, country: str | None = None,
                 account_name: str | None = None):
        if text is None:
            if path is None:
                raise ValueError("give a path or text")
            raw = Path(path).read_bytes()
            text = raw.decode("utf-8", errors="replace")
        self.tree = parse_ofx(text)
        self._institution = institution
        self._country = country
        self._account_name = account_name
        self._statements = self._read_statements()

    def _read_statements(self):
        org_node = self.tree.find("FI")
        org = org_node.fields.get("ORG") if org_node else None
        stmts = []
        for kind in ("STMTRS", "CCSTMTRS"):
            for st in self.tree.find_all(kind):
                currency = st.fields.get("CURDEF", "USD").upper()
                acct = st.find("BANKACCTFROM") or st.find("CCACCTFROM")
                if acct is None:
                    continue
                acct_id = acct.fields.get("ACCTID", "unknown")
                if kind == "CCSTMTRS":
                    atype = AccountType.CREDIT_CARD
                else:
                    atype = _ACCT_TYPES.get(acct.fields.get("ACCTTYPE", "").upper(), AccountType.OTHER)
                institution = self._institution or org or acct.fields.get("BANKID") or "Unknown bank"
                country = (self._country or _COUNTRY_BY_CURRENCY.get(currency, "US")).upper()
                info = AccountInfo(
                    external_key=f"ofx:{institution.lower()}:{acct_id}",
                    name=self._account_name or f"{institution} {atype.value} …{acct_id[-4:]}",
                    type=atype,
                    currency=currency,
                    institution=institution,
                    country=country,
                )
                stmts.append((info, st))
        if not stmts:
            raise ValueError("no bank or credit card statements found in OFX file")
        return stmts

    def list_accounts(self) -> list[AccountInfo]:
        return [info for info, _ in self._statements]

    def fetch_transactions(self, since: date | None = None) -> list[RawTransaction]:
        out = []
        for info, st in self._statements:
            for t in st.find_all("STMTTRN"):
                f = t.fields
                if "DTPOSTED" not in f or "TRNAMT" not in f:
                    continue
                posted = _ofx_date(f["DTPOSTED"])
                if since and posted < since:
                    continue
                desc = " ".join(x for x in (f.get("NAME", ""), f.get("MEMO", "")) if x).strip()
                out.append(RawTransaction(
                    account_key=info.external_key,
                    posted_at=posted,
                    amount_minor=to_minor(parse_amount(f["TRNAMT"]), info.currency),
                    currency=info.currency,
                    descriptor=desc or f.get("TRNTYPE", "UNKNOWN"),
                    external_id=f.get("FITID"),
                ))
        return out

    def fetch_balances(self) -> list[BalanceInfo]:
        out = []
        for info, st in self._statements:
            bal = st.find("LEDGERBAL")
            if bal is None or "BALAMT" not in bal.fields or "DTASOF" not in bal.fields:
                continue
            out.append(BalanceInfo(
                account_key=info.external_key,
                date=_ofx_date(bal.fields["DTASOF"]),
                balance_minor=to_minor(parse_amount(bal.fields["BALAMT"]), info.currency),
                currency=info.currency,
            ))
        return out
