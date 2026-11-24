import io
import json
from datetime import date
from decimal import Decimal

import pytest

from finapp.fx import RateMissing, convert_minor, parse_valet, rate, sync_boc
from finapp.ingest import CsvAdapter, run_import
from finapp.reports import foreign_account_report, net_worth

VALET = {
    "observations": [
        {"d": "2026-11-02", "FXUSDCAD": {"v": "1.3900"}},
        {"d": "2026-11-03", "FXUSDCAD": {"v": "1.4000"}},
        {"d": "2026-11-11", "FXUSDCAD": {}},  # holiday: no value
    ]
}


def test_parse_valet_skips_missing_days():
    rows = parse_valet(VALET, "FXUSDCAD", "USD", "CAD")
    assert [(r[0], r[3]) for r in rows] == [(date(2026, 11, 2), Decimal("1.3900")), (date(2026, 11, 3), Decimal("1.4000"))]


def test_sync_boc_with_fake_opener(repo):
    seen = {}

    def opener(url, timeout):
        seen["url"] = url
        return io.StringIO(json.dumps(VALET))

    assert sync_boc(repo, start=date(2026, 11, 1), end=date(2026, 11, 20), opener=opener) == 2
    assert "FXUSDCAD" in seen["url"] and "start_date=2026-11-01" in seen["url"]
    assert repo.latest_fx_date("USD", "CAD", "boc") == date(2026, 11, 3)


def test_rate_inverse_and_staleness(repo):
    repo.upsert_fx(parse_valet(VALET, "FXUSDCAD", "USD", "CAD"))
    r, _, d = rate(repo, "CAD", "USD", date(2026, 11, 5))  # carries forward from Nov 3, inverted
    assert d == date(2026, 11, 3) and r == Decimal(1) / Decimal("1.4")
    assert convert_minor(repo, 140000, "CAD", "USD", date(2026, 11, 5)) == 100000
    assert convert_minor(repo, 100000, "USD", "CAD", date(2026, 11, 3)) == 140000
    with pytest.raises(RateMissing):
        rate(repo, "USD", "CAD", date(2026, 12, 31))  # > 7 days after the last rate


def _seed(repo, us_checking, ca_chequing):
    repo.upsert_fx(parse_valet(VALET, "FXUSDCAD", "USD", "CAD"))
    run_import(repo, CsvAdapter(None, us_checking, text="Date,Description,Amount,Balance\n2026-11-03,PAY,100.00,5000.00\n"))
    run_import(repo, CsvAdapter(None, ca_chequing, text=(
        "Date,Description,Amount,Balance\n"
        "2026-11-02,DEPOSIT,8000.00,14000.00\n"
        "2026-11-03,RENT,-7000.00,7000.00\n"
    )))


def test_net_worth_converts_to_usd(repo, us_checking, ca_chequing):
    _seed(repo, us_checking, ca_chequing)
    nw = net_worth(repo, date(2026, 11, 3), "USD")
    assert nw.missing == []
    assert nw.total_minor == 500000 + 500000  # 7000 CAD / 1.4 = 5000 USD


def test_foreign_report_flags_fbar_from_a_one_day_peak(repo, us_checking, ca_chequing):
    _seed(repo, us_checking, ca_chequing)
    rep = foreign_account_report(repo, 2026, through=date(2026, 11, 3))
    assert rep["approximate"] is True  # no Treasury rate stored yet
    (acct,) = rep["accounts"]
    assert acct["max_minor"] == 1400000 and acct["max_date"] == "2026-11-02"
    assert rep["sum_of_max_usd_minor"] == 1000000  # 14,000 CAD at 1.40 = exactly $10,000
    assert rep["fbar_required"] is False  # threshold is *over* $10,000

    repo.upsert_fx([(date(2026, 12, 31), "USD", "CAD", Decimal("1.3000"), "treasury")])
    rep = foreign_account_report(repo, 2026, through=date(2026, 11, 3))
    assert rep["rate_source"] == "treasury" and rep["approximate"] is False
    assert rep["fbar_required"] is True  # 14,000 / 1.30 = $10,769.23
