"""Net worth and cross-border reporting built on balance snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from finapp.fx import RateMissing, convert_minor, rate
from finapp.repository import Repository

# FBAR: aggregate foreign account value over $10,000 at any time in the year.
FBAR_THRESHOLD_USD_MINOR = 10_000_00
# Form 8938, single filer living in the US: > $50,000 at year end or > $75,000 at any time.
F8938_YEAR_END_USD_MINOR = 50_000_00
F8938_ANY_TIME_USD_MINOR = 75_000_00


@dataclass
class NetWorth:
    on: date
    currency: str
    total_minor: int = 0
    lines: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def net_worth(repo: Repository, on: date, currency: str = "USD") -> NetWorth:
    """Sum the latest balance of each open account, converted at the rate for `on`."""
    nw = NetWorth(on=on, currency=currency.upper())
    for acct in repo.list_accounts():
        if acct["closed_at"]:
            continue
        snap = repo.latest_balance(acct["id"], on)
        if snap is None:
            nw.missing.append(f"{acct['name']}: no balance snapshot")
            continue
        try:
            converted = convert_minor(repo, snap["balance_minor"], snap["currency"], currency, on)
        except RateMissing as e:
            nw.missing.append(f"{acct['name']}: {e}")
            continue
        nw.total_minor += converted
        nw.lines.append({
            "account": acct["name"],
            "as_of": snap["date"],
            "balance_minor": snap["balance_minor"],
            "currency": snap["currency"],
            "converted_minor": converted,
        })
    return nw


def _daily_balances(repo: Repository, account_id: int, start: date, end: date) -> dict[date, int]:
    """Carry each snapshot forward day by day across [start, end]."""
    out: dict[date, int] = {}
    prior = repo.latest_balance(account_id, start - timedelta(days=1))
    current = prior["balance_minor"] if prior else None
    snaps = {date.fromisoformat(s["date"]): s["balance_minor"] for s in repo.snapshots(account_id, start, end)}
    d = start
    while d <= end:
        if d in snaps:
            current = snaps[d]
        if current is not None:
            out[d] = current
        d += timedelta(days=1)
    return out


def foreign_account_report(repo: Repository, year: int, through: date | None = None) -> dict:
    """FBAR / Form 8938 check for accounts held outside the US.

    Follows the FBAR method: each account's maximum value in the year, converted at
    the Treasury year-end rate, summed. If no Treasury rate is stored, the Bank of
    Canada year-end rate is used and the result is marked approximate.
    Balances only exist where snapshots were recorded, so a gap in snapshots can
    hide a peak: the report lists coverage per account.
    """
    start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    end = min(year_end, through or date.today())
    try:
        rate(repo, "USD", "CAD", year_end, source="treasury")
        rate_source, approximate = "treasury", False
    except RateMissing:
        rate_source, approximate = None, True

    accounts, sum_max, year_end_total, combined = [], 0, 0, {}
    for acct in repo.list_accounts():
        if not acct["is_foreign"]:
            continue
        daily = _daily_balances(repo, acct["id"], start, end)
        if not daily:
            accounts.append({"account": acct["name"], "currency": acct["currency"], "covered_days": 0})
            continue
        max_day = max(daily, key=lambda d: (daily[d], d))
        conv_on = year_end if rate_source == "treasury" else min(year_end, end)
        max_usd = convert_minor(repo, max(daily[max_day], 0), acct["currency"], "USD", conv_on, rate_source)
        last_usd = convert_minor(repo, daily[max(daily)], acct["currency"], "USD", conv_on, rate_source)
        sum_max += max_usd
        year_end_total += last_usd
        for d, v in daily.items():
            combined[d] = combined.get(d, 0) + convert_minor(repo, max(v, 0), acct["currency"], "USD", conv_on, rate_source)
        accounts.append({
            "account": acct["name"],
            "currency": acct["currency"],
            "max_minor": daily[max_day],
            "max_date": max_day.isoformat(),
            "max_usd_minor": max_usd,
            "covered_days": len(daily),
        })

    peak_day = max(combined, key=combined.get) if combined else None
    return {
        "year": year,
        "through": end.isoformat(),
        "rate_source": rate_source or "boc",
        "approximate": approximate,
        "accounts": accounts,
        "sum_of_max_usd_minor": sum_max,
        "peak_combined_usd_minor": combined.get(peak_day, 0) if peak_day else 0,
        "peak_combined_date": peak_day.isoformat() if peak_day else None,
        "latest_total_usd_minor": year_end_total,
        "fbar_required": sum_max > FBAR_THRESHOLD_USD_MINOR,
        "fbar_headroom_usd_minor": FBAR_THRESHOLD_USD_MINOR - sum_max,
        "form_8938_flag": (year_end_total > F8938_YEAR_END_USD_MINOR or sum_max > F8938_ANY_TIME_USD_MINOR),
    }
