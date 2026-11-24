from datetime import date
from decimal import Decimal

import pytest

from conftest import SGML_OFX, XML_CC_OFX
from finapp.ingest import CsvAdapter, CsvMapping, OfxAdapter, run_import
from finapp.money import parse_amount, to_minor


@pytest.mark.parametrize("text,expected", [
    ("$1,234.56", "1234.56"), ("(12.00)", "-12.00"), ("12.00-", "-12.00"),
    ("45.10 DR", "-45.10"), ("45.10CR", "45.10"), ("-3", "-3"),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == Decimal(expected)


def test_to_minor_rounds_half_even():
    assert to_minor(Decimal("1.005"), "USD") == 100
    assert to_minor(Decimal("1.015"), "USD") == 102


def test_csv_autodetects_columns_and_balances(us_checking):
    csv_text = (
        "Posting Date,Description,Amount,Balance\n"
        "11/03/2026,SQ *BLUE BOTTLE,-4.50,995.50\n"
        "11/03/2026,SQ *BLUE BOTTLE,-4.50,991.00\n"
        "11/15/2026,PAYROLL ACME,\"2,000.00\",2991.00\n"
    )
    a = CsvAdapter(None, us_checking, text=csv_text)
    txns = a.fetch_transactions()
    assert [t.amount_minor for t in txns] == [-450, -450, 200000]
    assert txns[0].posted_at == date(2026, 11, 3)
    bals = {b.date: b.balance_minor for b in a.fetch_balances()}
    assert bals == {date(2026, 11, 3): 99100, date(2026, 11, 15): 299100}


def test_csv_debit_credit_columns_newest_first(ca_chequing):
    # 13/11 forces day-first parsing; newest-first file order for balances.
    csv_text = (
        "Date,Details,Withdrawals,Deposits,Balance\n"
        "13/11/2026,E-TRANSFER,,500.00,1600.00\n"
        "02/11/2026,ROGERS,60.00,,1100.00\n"
        "02/11/2026,LOBLAWS,40.00,,1160.00\n"
    )
    a = CsvAdapter(None, ca_chequing, text=csv_text)
    txns = a.fetch_transactions()
    assert txns[0].posted_at == date(2026, 11, 13)
    assert [t.amount_minor for t in txns] == [50000, -6000, -4000]
    bals = {b.date: b.balance_minor for b in a.fetch_balances()}
    assert bals[date(2026, 11, 2)] == 110000  # first row of the day in a newest-first file


def test_csv_negate_for_credit_cards(us_checking):
    a = CsvAdapter(None, us_checking, CsvMapping(negate=True), text="Date,Description,Amount\n2026-11-01,AMAZON,25.00\n")
    assert a.fetch_transactions()[0].amount_minor == -2500


def test_csv_missing_columns_is_clear_error(us_checking):
    with pytest.raises(ValueError, match="date/description"):
        CsvAdapter(None, us_checking, text="Foo,Bar\n1,2\n")


def test_ofx_sgml_bank_statement():
    a = OfxAdapter(text=SGML_OFX)
    (acct,) = a.list_accounts()
    assert acct.currency == "CAD" and acct.country == "CA" and acct.is_foreign
    assert acct.institution == "TD"
    txns = a.fetch_transactions()
    assert [(t.posted_at, t.amount_minor) for t in txns] == [(date(2026, 11, 3), -4520), (date(2026, 11, 10), 100000)]
    assert txns[0].descriptor == "LOBLAWS #123 POS" and txns[0].external_id == "A1"
    (bal,) = a.fetch_balances()
    assert bal.balance_minor == 395480 and bal.date == date(2026, 11, 20)


def test_ofx_xml_credit_card():
    a = OfxAdapter(text=XML_CC_OFX, institution="Amex")
    (acct,) = a.list_accounts()
    assert acct.type.value == "credit_card" and not acct.is_foreign
    assert len(a.fetch_transactions()) == 2


def test_reimport_is_idempotent(repo, us_checking):
    text = "Date,Description,Amount\n2026-11-01,A,-1.00\n2026-11-01,A,-1.00\n2026-11-02,B,-2.00\n"
    first = run_import(repo, CsvAdapter(None, us_checking, text=text))
    second = run_import(repo, CsvAdapter(None, us_checking, text=text))
    assert (first.inserted, first.duplicates) == (3, 0)  # two identical same-day txns both kept
    assert (second.inserted, second.duplicates) == (0, 3)


def test_overlapping_exports_only_add_new_rows(repo, us_checking):
    run_import(repo, CsvAdapter(None, us_checking, text="Date,Description,Amount\n2026-11-01,A,-1.00\n2026-11-02,B,-2.00\n"))
    res = run_import(repo, CsvAdapter(None, us_checking,
                                      text="Date,Description,Amount\n2026-11-02,B,-2.00\n2026-11-03,C,-3.00\n"))
    assert (res.inserted, res.duplicates) == (1, 1)


def test_dedup_across_sources_ignores_descriptor(repo, ca_chequing):
    """The same TD transactions via OFX and via a CSV with different descriptors."""
    ofx = OfxAdapter(text=SGML_OFX)
    run_import(repo, ofx)
    key = ofx.list_accounts()[0].external_key
    same_account = ca_chequing.model_copy(update={"external_key": key})
    csv_text = "Date,Description,Amount\n2026-11-03,Loblaws,-45.20\n2026-11-10,Interac e-Transfer,1000.00\n"
    res = run_import(repo, CsvAdapter(None, same_account, text=csv_text))
    assert (res.inserted, res.duplicates) == (0, 2)


def test_new_transactions_get_default_split(repo, us_checking):
    run_import(repo, CsvAdapter(None, us_checking, text="Date,Description,Amount\n2026-11-01,PAY,100.00\n2026-11-02,FOOD,-5.00\n"))
    txns = {t["raw_descriptor"]: t for t in repo.list_transactions()}
    pay, food = repo.splits_for(txns["PAY"]["id"]), repo.splits_for(txns["FOOD"]["id"])
    assert pay[0]["flow_type"] == "income" and pay[0]["amount_minor"] == 10000
    assert food[0]["flow_type"] == "spend" and food[0]["budget_include"] == 1


def test_sync_state_records_success_and_failure(repo, us_checking):
    run_import(repo, CsvAdapter(None, us_checking, text="Date,Description,Amount\n2026-11-01,A,-1.00\n"))
    (acct,) = repo.list_accounts()
    assert acct["sync_status"] == "ok" and acct["last_success_at"]

    class Broken(CsvAdapter):
        def fetch_transactions(self, since=None):
            raise ConnectionError("provider down")

    with pytest.raises(ConnectionError):
        run_import(repo, Broken(None, us_checking, text="Date,Description,Amount\n2026-11-01,A,-1.00\n"))
    (acct,) = repo.list_accounts()
    assert acct["sync_status"] == "error" and "provider down" in acct["sync_error"]
