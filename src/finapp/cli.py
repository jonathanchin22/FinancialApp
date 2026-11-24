"""Command line: finapp <command>. Database path from --db or $FINAPP_DB (default data/finapp.db)."""

from __future__ import annotations

import argparse
import json
from datetime import date

from finapp.db import connect
from finapp.fx import sync_boc
from finapp.ingest import CsvAdapter, CsvMapping, OfxAdapter, run_import
from finapp.models import AccountInfo, AccountType
from finapp.money import format_minor
from finapp.reports import foreign_account_report, net_worth
from finapp.repository import Repository


def _print_import(res) -> None:
    for a in res.accounts:
        print(f"  {a.name}: {a.inserted} new, {a.duplicates} duplicates skipped, {a.snapshots} balance snapshots")
    print(f"Total: {res.inserted} new, {res.duplicates} duplicates")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="finapp", description="FinancialApp command line")
    p.add_argument("--db", help="database file (default $FINAPP_DB or data/finapp.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create or migrate the database")

    c = sub.add_parser("import-csv", help="import a bank CSV export")
    c.add_argument("file")
    c.add_argument("--account-key", required=True, help="stable id, e.g. csv:chase:checking")
    c.add_argument("--name", required=True)
    c.add_argument("--type", required=True, choices=[t.value for t in AccountType])
    c.add_argument("--currency", required=True)
    c.add_argument("--institution", required=True)
    c.add_argument("--country", required=True, help="where the account is held: US, CA, ...")
    c.add_argument("--negate", action="store_true", help="flip signs (purchases exported as positive)")
    c.add_argument("--date-format", help="strptime format if auto-detection is ambiguous")
    for col in ("date", "description", "amount", "debit", "credit", "balance"):
        c.add_argument(f"--{col}-col")
    c.add_argument("--skip-rows", type=int, default=0)

    o = sub.add_parser("import-ofx", help="import an OFX/QFX file")
    o.add_argument("file")
    o.add_argument("--country")
    o.add_argument("--institution")
    o.add_argument("--name")

    f = sub.add_parser("fx-sync", help="fetch Bank of Canada USD/CAD rates")
    f.add_argument("--start", type=date.fromisoformat)

    r = sub.add_parser("fx-set", help="store a rate by hand, e.g. the Treasury year-end rate")
    r.add_argument("date", type=date.fromisoformat)
    r.add_argument("cad_per_usd")
    r.add_argument("--source", default="treasury", choices=["treasury", "manual"])

    s = sub.add_parser("snapshot", help="record an account balance by hand")
    s.add_argument("account_key")
    s.add_argument("balance")
    s.add_argument("--date", type=date.fromisoformat, default=date.today())

    sub.add_parser("accounts", help="list accounts and sync state")

    n = sub.add_parser("networth", help="net worth from latest balances")
    n.add_argument("--currency", default="USD")
    n.add_argument("--on", type=date.fromisoformat, default=date.today())

    fa = sub.add_parser("foreign", help="FBAR / Form 8938 check for foreign accounts")
    fa.add_argument("year", type=int)

    args = p.parse_args(argv)
    repo = Repository(connect(args.db))

    if args.cmd == "init":
        print("Database ready.")
    elif args.cmd == "import-csv":
        info = AccountInfo(external_key=args.account_key, name=args.name, type=AccountType(args.type),
                           currency=args.currency.upper(), institution=args.institution,
                           country=args.country.upper())
        mapping = CsvMapping(date_col=args.date_col, description_col=args.description_col,
                             amount_col=args.amount_col, debit_col=args.debit_col,
                             credit_col=args.credit_col, balance_col=args.balance_col,
                             date_format=args.date_format, negate=args.negate, skip_rows=args.skip_rows)
        _print_import(run_import(repo, CsvAdapter(args.file, info, mapping)))
    elif args.cmd == "import-ofx":
        _print_import(run_import(repo, OfxAdapter(args.file, country=args.country,
                                                  institution=args.institution, account_name=args.name)))
    elif args.cmd == "fx-sync":
        import urllib.error
        try:
            print(f"Stored {sync_boc(repo, start=args.start)} Bank of Canada rates.")
        except urllib.error.URLError as e:
            print(f"Could not reach the Bank of Canada: {e.reason}")
            return 1
    elif args.cmd == "fx-set":
        from decimal import Decimal
        repo.upsert_fx([(args.date, "USD", "CAD", Decimal(args.cad_per_usd), args.source)])
        print("Rate stored.")
    elif args.cmd == "snapshot":
        from finapp.money import parse_amount, to_minor
        acct = repo.get_account(repo.account_id(args.account_key))
        repo.record_snapshot(acct["id"], args.date, to_minor(parse_amount(args.balance), acct["currency"]),
                             acct["currency"], "manual")
        print("Snapshot recorded.")
    elif args.cmd == "accounts":
        for a in repo.list_accounts():
            foreign = " [foreign]" if a["is_foreign"] else ""
            print(f"{a['id']:>3}  {a['name']} ({a['currency']}, {a['jurisdiction']}){foreign}"
                  f"  sync={a['sync_status']} last={a['last_success_at'] or '-'}")
    elif args.cmd == "networth":
        nw = net_worth(repo, args.on, args.currency)
        for line in nw.lines:
            print(f"  {line['account']}: {format_minor(line['balance_minor'], line['currency'])}"
                  f" -> {format_minor(line['converted_minor'], nw.currency)} (as of {line['as_of']})")
        for m in nw.missing:
            print(f"  ! {m}")
        print(f"Net worth: {format_minor(nw.total_minor, nw.currency)}")
    elif args.cmd == "foreign":
        rep = foreign_account_report(repo, args.year)
        print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
