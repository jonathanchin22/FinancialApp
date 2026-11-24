"""Exchange rates: Bank of Canada daily history, plus exact conversion.

Bank of Canada Valet series FXUSDCAD = Canadian dollars per 1 US dollar, published
for each Canadian business day. Stored as base=USD, quote=CAD, source='boc'.
Treasury year-end rates (used for FBAR) can be stored with source='treasury'.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from finapp.money import from_minor, minor_digits
from finapp.repository import Repository

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"
BOC_SERIES = {("USD", "CAD"): "FXUSDCAD"}
MAX_STALENESS_DAYS = 7  # weekends + holidays; anything older is treated as missing


class RateMissing(LookupError):
    pass


def parse_valet(payload: dict, series: str, base: str, quote: str) -> list[tuple]:
    rows = []
    for obs in payload.get("observations", []):
        cell = obs.get(series) or {}
        value = cell.get("v")
        if value in (None, ""):
            continue  # no rate published that day
        rows.append((date.fromisoformat(obs["d"]), base, quote, Decimal(value), "boc"))
    return rows


def fetch_boc(start: date, end: date, base: str = "USD", quote: str = "CAD", opener=None) -> list[tuple]:
    series = BOC_SERIES[(base.upper(), quote.upper())]
    qs = urllib.parse.urlencode({"start_date": start.isoformat(), "end_date": end.isoformat()})
    url = VALET_URL.format(series=series) + "?" + qs
    open_ = opener or urllib.request.urlopen
    with open_(url, timeout=30) as resp:
        payload = json.load(resp)
    return parse_valet(payload, series, base.upper(), quote.upper())


def sync_boc(repo: Repository, start: date | None = None, end: date | None = None, opener=None) -> int:
    """Fetch rates since the last stored date (or `start`) through `end` (default today)."""
    end = end or date.today()
    if start is None:
        last = repo.latest_fx_date("USD", "CAD", "boc")
        start = (last + timedelta(days=1)) if last else date(end.year - 2, 1, 1)
    if start > end:
        return 0
    return repo.upsert_fx(fetch_boc(start, end, opener=opener))


def rate(repo: Repository, base: str, quote: str, on: date, source: str | None = None) -> tuple[Decimal, str, date]:
    """Units of `quote` per 1 `base` on `on`, using the latest rate within the staleness window.

    Returns (rate, source, rate_date). Falls back to the inverse pair.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return Decimal(1), "identity", on
    for b, q, invert in ((base, quote, False), (quote, base, True)):
        row = repo.fx_on_or_before(on, b, q, source)
        if row is not None and (on - date.fromisoformat(row["date"])).days <= MAX_STALENESS_DAYS:
            r = Decimal(row["rate"])
            return (Decimal(1) / r if invert else r), row["source"], date.fromisoformat(row["date"])
    raise RateMissing(f"no {base}->{quote} rate within {MAX_STALENESS_DAYS} days of {on}")


def convert_minor(repo: Repository, amount_minor: int, from_cur: str, to_cur: str, on: date,
                  source: str | None = None) -> int:
    r, _, _ = rate(repo, from_cur, to_cur, on, source)
    value = from_minor(amount_minor, from_cur) * r
    digits = minor_digits(to_cur)
    return int(value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_EVEN).scaleb(digits))
